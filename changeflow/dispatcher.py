"""分发器：按主键分区保序、跨主键并行、确认位点、超时/重启重投。

并发模型
- 单个控制线程负责：扫描日志、把变更放入各主键的有序队列、派发、
  处理确认事件、回收超时未确认的在途变更（避免调度数据加锁）。
- ThreadPoolExecutor 执行消费者 handler；不同主键可同时在线程池里运行，
  同一主键同一时刻至多有一条变更在途，因此同一主键严格按 seq 投递。
- 一条变更在 delivery_timeout 内未确认（handler 崩溃/卡住/ack 丢失）时，
  会被重新放回该主键队列队首重新投递 -> 可能重复投递，消费者必须幂等。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .log import ChangeLog
from .model import Change
from .offsets import OffsetStore

log = logging.getLogger("changeflow")

Handler = Callable[[Change, Callable[[int], None]], None]


@dataclass
class _InFlight:
    seq: int
    key: str
    deadline: float
    attempt: int
    acked: bool = False


@dataclass
class _KeyState:
    waiting: List[Tuple[float, int]] = field(default_factory=list)  # (最早可投递时间, seq)
    current: Optional[_InFlight] = None
    delivered: List[int] = field(default_factory=list)  # 实际投递记录（含重复投递）


def assert_per_key_order(
    delivered: Dict[str, List[int]],
    expected: Dict[str, List[int]],
) -> None:
    """乱序检测：对每个主键断言投递顺序 == 产生顺序（重复投递除外）。

    - 允许同一个在途 seq 因超时被重复投递（出现连续/稍后的相等值）；
    - 去重后的投递序列必须严格递增，且与期望位点集合完全一致；
    - 任意违反都会抛出 AssertionError；全部通过即"乱序数量为 0"。
    """
    assert set(delivered) == set(expected), "主键集合不一致"
    out_of_order = 0
    for key, deliveries in delivered.items():
        unique: List[int] = []
        for seq in deliveries:
            if not unique or seq != unique[-1]:
                unique.append(seq)
        want = sorted(expected[key])
        if unique != want:
            out_of_order += 1
            raise AssertionError(
                f"主键 {key!r} 投递顺序 {deliveries} 去重后 {unique}，"
                f"期望 {want}：出现乱序"
            )
        if any(b < a for a, b in zip(deliveries, deliveries[1:])):
            out_of_order += 1
            raise AssertionError(f"主键 {key!r} 投递记录 {deliveries[key]} 存在回退")
    assert out_of_order == 0, f"检测到 {out_of_order} 个主键乱序"


def global_reorder(results: Iterable[Any]) -> List[Any]:
    """把并行消费产生的结果按全局位点 seq 重新排序为全局顺序。

    接受 Change，或带 seq 属性/首元素为 seq 的 (seq, payload) 元组。
    """
    def seq_of(item: Any) -> int:
        if isinstance(item, Change):
            return item.seq
        if hasattr(item, "seq"):
            return int(item.seq)
        return int(item[0])

    return sorted(results, key=seq_of)


class Dispatcher:
    def __init__(
        self,
        change_log: ChangeLog,
        offsets: OffsetStore,
        handler: Handler,
        workers: int = 8,
        delivery_timeout: float = 0.3,
        tick: float = 0.02,
        scan_batch: int = 256,
        auto_ack: bool = True,
    ):
        self.log = change_log
        self.offsets = offsets
        self.handler = handler
        self.delivery_timeout = delivery_timeout
        self.tick = tick
        self.scan_batch = scan_batch
        self.auto_ack = auto_ack

        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cf-worker")
        self._cv = threading.Condition()
        self._stop = False
        self._keys: Dict[str, _KeyState] = {}
        self._changes: Dict[int, Change] = {}
        self._loaded_seq = 0
        self._active = 0
        self._events: "queue.Queue[Tuple[str, int]]" = queue.Queue()
        self.deliveries = 0  # 投递总次数（含重复投递，用于断言/观测）
        self.errors = 0
        self._thread = threading.Thread(target=self._run, name="cf-dispatch", daemon=True)

    # ---- 生命周期 -----------------------------------------------------

    def start(self) -> None:
        """启动分发；恢复时从位点水位线之后重新加载，未确认变更自动重投。"""
        self._loaded_seq = self.offsets.watermark  # 水位线之前的无需再读
        self._thread.start()

    def wake(self) -> None:
        with self._cv:
            self._cv.notify_all()

    def stop(self, timeout: float = 10.0) -> None:
        """优雅停止：等待所有已加载变更被确认后再退出。"""
        deadline = time.monotonic() + timeout
        with self._cv:
            while not self._drained_locked() and time.monotonic() < deadline:
                self._cv.wait(0.05)
        self._terminate()

    def crash_stop(self) -> None:
        """模拟崩溃：立即停止调度与待执行任务，不等待任何确认。"""
        self._terminate()

    def _terminate(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._thread.join(timeout=1.0)
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # 旧版本 Python 无 cancel_futures
            self._pool.shutdown(wait=False)

    def _drained_locked(self) -> bool:
        return (
            self._active == 0
            and all(not ks.waiting and ks.current is None for ks in self._keys.values())
        )

    def join_drained(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while not self._drained_locked() and time.monotonic() < deadline:
                self._cv.wait(0.05)
            return self._drained_locked()

    # ---- 确认 ---------------------------------------------------------

    def ack(self, seq: int) -> None:
        """消费者确认位点：先持久化位点，再由控制线程释放该主键的在途槽位。"""
        self._events.put(("ack", seq))
        self.wake()

    # ---- 控制线程主循环 ----------------------------------------------

    def _run(self) -> None:
        with self._cv:
            while True:
                self._drain_events_locked()
                self._scan_locked()
                self._reap_locked()
                self._pump_locked()
                if self._stop and self._drained_locked():
                    break
                if self._stop:
                    break
                self._cv.wait(self.tick)

    def _drain_events_locked(self) -> None:
        while True:
            try:
                kind, seq = self._events.get_nowait()
            except queue.Empty:
                return
            if kind == "ack":
                change = self._changes.get(seq)
                if change is None:
                    self.offsets.ack(seq)  # 延迟到达的确认，仍然落盘
                    continue
                ks = self._keys[change.key]
                if ks.current is not None and ks.current.seq == seq and not ks.current.acked:
                    self.offsets.ack(seq)
                    ks.current.acked = True
                    ks.current = None
                else:
                    self.offsets.ack(seq)  # 重复/过期确认：幂等，只落盘
            elif kind == "error":
                change = self._changes.get(seq)
                if change is None:
                    continue
                ks = self._keys[change.key]
                if ks.current is not None and ks.current.seq == seq and not ks.current.acked:
                    self.errors += 1
                    ks.current = None
                    # 稍后重试，放回队首
                    ks.waiting.insert(0, (time.monotonic() + self.tick * 5, seq))

    def _scan_locked(self) -> None:
        batch = self.log.read_from(self._loaded_seq, self.scan_batch)
        for change in batch:
            self._loaded_seq = max(self._loaded_seq, change.seq)
            if self.offsets.is_acked(change.seq):
                continue  # 重启恢复：水位线以上但已确认过的（洞）不再投递
            self._changes[change.seq] = change
            ks = self._keys.setdefault(change.key, _KeyState())
            if not any(seq == change.seq for _, seq in ks.waiting):
                ks.waiting.append((0.0, change.seq))
        if len(batch) == self.scan_batch:
            self._scan_locked()  # 继续读完，保证启动时完整恢复

    def _reap_locked(self) -> None:
        now = time.monotonic()
        for ks in self._keys.values():
            cur = ks.current
            if cur is not None and not cur.acked and now >= cur.deadline:
                # 超时未确认：handler 卡住、崩溃或 ack 丢失 -> 重新投递（可能重复）
                log.warning("seq=%s key=%r 第 %s 次投递超时，重新投递",
                            cur.seq, cur.key, cur.attempt)
                ks.current = None
                ks.waiting.insert(0, (now, cur.seq))

    def _pump_locked(self) -> None:
        now = time.monotonic()
        for ks in self._keys.values():
            if ks.current is not None:
                continue  # 同一主键同一时刻只允许一条在途
            while ks.waiting:
                not_before, seq = ks.waiting[0]
                if now < not_before:
                    return  # 队首都没到时间，后面的也不能越过它（保序）
                ks.waiting.pop(0)
                change = self._changes.get(seq)
                if change is None or self.offsets.is_acked(seq):
                    continue  # 竞态下已被确认
                attempt = self._attempt(ks, seq)
                ks.current = _InFlight(seq=seq, key=change.key,
                                       deadline=now + self.delivery_timeout, attempt=attempt)
                ks.delivered.append(seq)
                self.deliveries += 1
                self._active += 1
                self._pool.submit(self._execute, change)
                break

    def _attempt(self, ks: _KeyState, seq: int) -> int:
        return sum(1 for s in ks.delivered if s == seq) + 1

    def _execute(self, change: Change) -> None:
        try:
            self.handler(change, self.ack)
        except Exception:  # 消费者处理失败：稍后重新投递
            log.exception("handler 处理 seq=%s 失败", change.seq)
            self._events.put(("error", change.seq))
        else:
            if self.auto_ack and not self.offsets.is_acked(change.seq):
                self.ack(change.seq)
        finally:
            with self._cv:
                self._active -= 1
                self._cv.notify_all()

    # ---- 观测 ---------------------------------------------------------

    def delivered_for(self, key: str) -> List[int]:
        """某主键的实际投递位点记录（含重复投递），用于顺序断言。"""
        ks = self._keys.get(key)
        return list(ks.delivered) if ks else []

    def delivery_map(self) -> Dict[str, List[int]]:
        return {key: list(ks.delivered) for key, ks in self._keys.items()}
