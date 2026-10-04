"""带隔离舱（bulkhead）的线程池：多业务共享一个池，按类别隔离并发与排队。

核心语义
--------
- 总舱位 = 各类别 max_concurrency 之和，即每个类别都拥有自己的保证舱位。
- 类别当前并发低于自己的 max_concurrency 时，可以借用其他类别的空闲舱位
  （此时该类别并发会暂时超过自己的上限，但全局并发永远不超过总舱位）。
- 收回：空闲舱位优先分配给"低于保证水位且有排队任务"的类别，其次才是借用者。
  因此原属类别一旦有新需求，最迟在一个借用任务结束后即可收回舱位。
- 每个类别有独立的 FIFO 等待队列与 max_queue 上限，超限按策略拒绝并计数。
- 任务抛异常不会泄漏舱位（worker 侧 try/finally 归还）。
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional, Tuple


class RejectedExecutionError(RuntimeError):
    """提交被拒绝（ABORT 策略下抛出）。"""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class PoolShutdownError(RuntimeError):
    """线程池已关闭，拒绝新的提交。"""


@dataclass(frozen=True)
class CategoryConfig:
    """单个类别的隔离配置。

    max_concurrency: 该类别的保证舱位数（也是触发借用判定的水位线）。
    max_queue:       该类别等待队列的最大长度（不含正在执行的任务）。
    policy:          队列满时的拒绝策略，默认 ABORT（抛 RejectedExecutionError）。
    """

    max_concurrency: int
    max_queue: int
    policy: Optional["RejectionPolicy"] = None


@dataclass(frozen=True)
class CategoryStats:
    """类别运行指标快照。"""

    category: str
    max_concurrency: int  # 保证舱位数
    max_queue: int        # 排队上限
    active: int           # 当前正在执行的任务数
    borrowed: int         # 当前借用他类舱位的数量 = max(0, active - max_concurrency)
    queued: int           # 当前排队等待的任务数
    submitted: int        # 累计接受（未拒绝）的任务数
    rejected: int         # 累计被拒绝的任务数
    completed: int        # 累计正常完成的任务数
    failed: int           # 累计抛异常的任务数（舱位已正常归还）


class RejectionPolicy:
    """队列满时的拒绝策略基类。计数在策略执行前已完成。"""

    name = "custom"

    def reject(self, pool: "BulkheadThreadPool", category: str, future: Future) -> None:
        raise NotImplementedError


class AbortPolicy(RejectionPolicy):
    """默认策略：向提交者抛出 RejectedExecutionError，future 同时被取消。"""

    name = "abort"

    def reject(self, pool: "BulkheadThreadPool", category: str, future: Future) -> None:
        future.cancel()
        raise RejectedExecutionError(
            category, f"category {category!r} queue is full, task rejected"
        )


class DiscardPolicy(RejectionPolicy):
    """静默丢弃：不抛异常，返回的 future 处于 cancelled 状态。"""

    name = "discard"

    def reject(self, pool: "BulkheadThreadPool", category: str, future: Future) -> None:
        future.cancel()


ABORT = AbortPolicy()
DISCARD = DiscardPolicy()


class _Entry:
    """队列中的一个待执行任务。"""

    __slots__ = ("seq", "category", "fn", "args", "kwargs", "future", "ready")

    def __init__(self, seq, category, fn, args, kwargs, future):
        self.seq = seq
        self.category = category
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.future = future
        self.ready = False  # 是否已被分配舱位（进入 ready 队列）


class BulkheadThreadPool:
    """带隔离舱的线程池。

    用法::

        pool = BulkheadThreadPool({
            "fast": CategoryConfig(max_concurrency=2, max_queue=4),
            "slow": CategoryConfig(max_concurrency=2, max_queue=4),
        })
        fut = pool.submit("fast", fn, arg1, key=arg2)
        print(pool.stats("fast").rejected)
        pool.shutdown()
    """

    def __init__(self, categories: Dict[str, CategoryConfig]):
        if not categories:
            raise ValueError("at least one category is required")
        self._configs: Dict[str, CategoryConfig] = dict(categories)
        for name, cfg in self._configs.items():
            if cfg.max_concurrency < 1:
                raise ValueError(f"category {name!r}: max_concurrency must be >= 1")
            if cfg.max_queue < 0:
                raise ValueError(f"category {name!r}: max_queue must be >= 0")

        self._total_slots = sum(c.max_concurrency for c in self._configs.values())

        self._cond = threading.Condition()
        self._queues: Dict[str, Deque[_Entry]] = {
            name: deque() for name in self._configs
        }
        self._ready: Deque[_Entry] = deque()  # 已分配舱位、等待 worker 领取
        self._active: Dict[str, int] = {name: 0 for name in self._configs}
        self._submitted: Dict[str, int] = {name: 0 for name in self._configs}
        self._rejected: Dict[str, int] = {name: 0 for name in self._configs}
        self._completed: Dict[str, int] = {name: 0 for name in self._configs}
        self._failed: Dict[str, int] = {name: 0 for name in self._configs}
        self._seq = itertools.count()
        self._idle_workers = self._total_slots  # 初始全部空闲
        self._stopping = False
        self._shutdown = False

        self._workers = [
            threading.Thread(
                target=self._worker_loop,
                name=f"bulkhead-worker-{i}",
                daemon=True,
            )
            for i in range(self._total_slots)
        ]
        for t in self._workers:
            t.start()

    # ------------------------------------------------------------------ API

    def submit(self, category: str, fn: Callable, /, *args, **kwargs) -> Future:
        """提交任务。队列满时按类别策略拒绝（默认抛 RejectedExecutionError）。"""
        if category not in self._configs:
            raise KeyError(f"unknown category: {category!r}")
        future: Future = Future()
        entry = _Entry(self._next_seq(), category, fn, args, kwargs, future)
        with self._cond:
            if self._shutdown or self._stopping:
                raise PoolShutdownError("pool has been shut down")
            cfg = self._configs[category]
            queue = self._queues[category]
            # 有空闲舱位时新任务会被立刻分派（泵入 ready），不占用排队额度；
            # 只有分派不出去、确实要排队等待的部分才计入 max_queue 上限。
            dispatchable = max(0, self._idle_workers - len(self._ready))
            would_queue = max(0, len(queue) + 1 - dispatchable)
            if would_queue > cfg.max_queue:
                self._rejected[category] += 1
                policy = cfg.policy or ABORT
                policy.reject(self, category, future)
                return future  # 仅 DISCARD 类策略会走到这里
            queue.append(entry)
            self._submitted[category] += 1
            self._pump()
        return future

    def stats(self, category: str) -> CategoryStats:
        """单个类别的指标快照。"""
        with self._cond:
            return self._stats_locked(category)

    def all_stats(self) -> Dict[str, CategoryStats]:
        """所有类别的指标快照。"""
        with self._cond:
            return {name: self._stats_locked(name) for name in self._configs}

    def available_slots(self, category: str) -> int:
        """该类别当前立即可用的舱位数（含可借用的全局空闲舱位）。"""
        with self._cond:
            return self._available_locked(category)

    def total_available_slots(self) -> int:
        """全局空闲舱位数。"""
        with self._cond:
            return self._total_slots - sum(self._active.values())

    @property
    def total_slots(self) -> int:
        return self._total_slots

    def shutdown(self, wait: bool = True, cancel_pending: bool = False) -> None:
        """关闭线程池。

        wait=True          等待所有已接受任务执行完毕（默认，队列会被排空）。
        cancel_pending=True 立即丢弃排队任务（对应 future 被取消），只等运行中任务。
        """
        with self._cond:
            self._shutdown = True
            if cancel_pending:
                self._stopping = True
                for queue in self._queues.values():
                    while queue:
                        queue.popleft().future.cancel()
                while self._ready:
                    self._ready.popleft().future.cancel()
            self._cond.notify_all()
        if wait:
            for t in self._workers:
                t.join()

    def __enter__(self) -> "BulkheadThreadPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    # ------------------------------------------------------------- internal

    def _next_seq(self) -> int:
        return next(self._seq)

    def _stats_locked(self, category: str) -> CategoryStats:
        cfg = self._configs[category]
        active = self._active[category]
        return CategoryStats(
            category=category,
            max_concurrency=cfg.max_concurrency,
            max_queue=cfg.max_queue,
            active=active,
            borrowed=max(0, active - cfg.max_concurrency),
            queued=len(self._queues[category]),
            submitted=self._submitted[category],
            rejected=self._rejected[category],
            completed=self._completed[category],
            failed=self._failed[category],
        )

    def _available_locked(self, category: str) -> int:
        cfg = self._configs[category]
        global_free = self._total_slots - sum(self._active.values())
        own_free = max(0, cfg.max_concurrency - self._active[category])
        return own_free + min(
            global_free - own_free,
            sum(
                max(0, self._configs[n].max_concurrency - self._active[n])
                for n in self._configs
                if n != category
            ),
        )

    def _pick_locked(self) -> Optional[_Entry]:
        """从等待队列中挑一个可分配舱位的任务（不入 ready，只选定）。

        第一优先级：active < max_concurrency 的类别（收回/使用保证舱位）。
        第二优先级：其余类别（借用空闲舱位）。
        同优先级内按入队先后（seq）选择。
        """
        best: Optional[_Entry] = None
        for name, queue in self._queues.items():
            if queue and self._active[name] < self._configs[name].max_concurrency:
                head = queue[0]
                if best is None or head.seq < best.seq:
                    best = head
        if best is not None:
            return best
        for queue in self._queues.values():
            if queue:
                head = queue[0]
                if best is None or head.seq < best.seq:
                    best = head
        return best

    def _pump(self) -> None:
        """持锁状态下，把可分派任务移入 ready 队列并唤醒 worker。"""
        moved = 0
        while self._idle_workers > len(self._ready):
            entry = self._pick_locked()
            if entry is None:
                break
            self._queues[entry.category].popleft()
            entry.ready = True
            self._ready.append(entry)
            moved += 1
        if moved:
            self._cond.notify_all()

    def _worker_loop(self) -> None:
        while True:
            with self._cond:
                while not self._ready:
                    if self._stopping:
                        return
                    if self._shutdown and not any(self._queues.values()):
                        return
                    self._cond.wait()
                entry = self._ready.popleft()
                self._idle_workers -= 1
                self._active[entry.category] += 1
            try:
                if not entry.future.set_running_or_notify_cancel():
                    continue  # 排队期间被取消：直接走 finally 归还舱位
                try:
                    result = entry.fn(*entry.args, **entry.kwargs)
                except BaseException as exc:  # noqa: BLE001 - 异常必须原样传给 future
                    entry.future.set_exception(exc)
                    with self._cond:
                        self._failed[entry.category] += 1
                else:
                    entry.future.set_result(result)
                    with self._cond:
                        self._completed[entry.category] += 1
            finally:
                # 无论任务成功、异常还是被取消，舱位都必须归还并触发再分派。
                with self._cond:
                    self._active[entry.category] -= 1
                    self._idle_workers += 1
                    self._pump()
