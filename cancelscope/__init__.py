"""cancelscope —— 超时与级联取消的结构化并发作用域（仅标准库）。

核心概念
--------
- ``Scope``：异步上下文管理器。作用域内 ``spawn()`` 的子任务被托管：
  * 父作用域超时（``timeout`` 到期）→ 所有存活子任务收到取消信号；
  * 子任务失败 → 按 ``FailurePolicy`` 决定是否提前结束整个作用域；
  * 作用域退出前，必然等待所有子任务（含其收尾代码）彻底结束，
    因此退出后 ``active_count() == 0``，后台不会残留任务。
- 时间可注入：``Scope`` 只依赖 ``Clock`` 协议（``now()`` / ``sleep()``）。
  生产用 ``RealClock``，测试用 ``VirtualClock``（手动推进、零真实等待）。

失败策略（FailurePolicy）
-------------------------
- ``FAIL_FAST``（默认）：任一子任务失败 → 立即取消其余子任务并提前结束
  作用域；退出时抛出首个失败（多个失败时打包为 ExceptionGroup）。
- ``WAIT_ALL``：子任务失败不打扰兄弟任务，等全部结束后抛出首个失败
  （多个失败时打包为 ExceptionGroup）。
- 注意：无论哪种策略，**超时总是级联取消所有存活子任务**。

边界规则
--------
- 子任务到期时刻 == 超时到期时刻时，**超时优先**（先注册的定时器先触发）。
- ``timeout=None`` 表示永不超时；``timeout<=0`` 表示立即超时。
- 取消信号对每个子任务只发送一次；收尾（cleanup）阶段不会被打断第二次。
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from enum import Enum
from typing import Any, Awaitable, Callable, Coroutine, List, Optional

__all__ = [
    "Clock",
    "RealClock",
    "VirtualClock",
    "FailurePolicy",
    "Scope",
    "ScopeTimeout",
    "TaskState",
    "active_count",
]


# ---------------------------------------------------------------------------
# 时钟：可注入的时间源
# ---------------------------------------------------------------------------

class Clock:
    """时钟协议：作用域的超时逻辑只依赖这两个方法。"""

    def now(self) -> float:  # pragma: no cover - 抽象定义
        raise NotImplementedError

    async def sleep(self, delay: float) -> None:  # pragma: no cover - 抽象定义
        raise NotImplementedError


class RealClock(Clock):
    """真实时钟，生产环境使用。"""

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(max(0.0, delay))


class VirtualClock(Clock):
    """虚拟时钟：时间只能由测试通过 ``advance()`` 显式推进。

    - ``sleep(d)`` 挂起直到虚拟时间前进 ``d`` 秒（不消耗真实时间）；
    - ``advance(d)`` 推进虚拟时间，并唤醒所有到期定时器；
    - ``settle()`` 不推进时间，只把当前已就绪的任务跑到稳定状态。

    实现说明：``advance``/``settle`` 借助事件循环的私有 ``_ready`` 队列
    判断“是否还有任务待调度”（CPython 3.10+ 可用），从而把被唤醒的任务
    级联跑完，测试因此是确定性的。
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)
        self._timers: List[list] = []  # 堆元素: [deadline, seq, future]
        self._seq = itertools.count()

    def now(self) -> float:
        return self._now

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        heapq.heappush(self._timers, [self._now + delay, next(self._seq), fut])
        try:
            await fut
        finally:
            fut.cancel()  # 已触发则无副作用；被取消则标记，触发时跳过

    async def advance(self, delay: float) -> None:
        """推进虚拟时间 ``delay`` 秒，唤醒沿途所有到期定时器。"""
        if delay < 0:
            raise ValueError("advance() 的步长不能为负")
        target = self._now + delay
        while True:
            await self._pump()
            due = [t for t in self._timers if not t[2].done() and t[0] <= target]
            if not due:
                break
            wake = min(t[0] for t in due)
            self._now = max(self._now, wake)
            while self._timers and (self._timers[0][2].done()
                                    or self._timers[0][0] <= self._now):
                _, _, fut = heapq.heappop(self._timers)
                if not fut.done():
                    fut.set_result(None)
        self._now = target
        await self._pump()

    async def settle(self) -> None:
        """不推进时间，把已就绪的任务跑到稳定（队列清空）。"""
        await self._pump()

    async def _pump(self) -> None:
        """让出执行权，直到事件循环的就绪队列连续两轮为空。"""
        loop = asyncio.get_running_loop()
        for _ in range(256):
            await asyncio.sleep(0)
            ready = getattr(loop, "_ready", None)
            if ready is not None and len(ready) == 0:
                await asyncio.sleep(0)
                if len(ready) == 0:
                    return


# ---------------------------------------------------------------------------
# 失败策略与异常
# ---------------------------------------------------------------------------

class FailurePolicy(Enum):
    """子任务失败时父作用域的应对策略。"""

    FAIL_FAST = "fail_fast"  # 立即取消其余子任务，提前结束（默认）
    WAIT_ALL = "wait_all"    # 不打扰兄弟任务，全部结束后抛出首个失败


class ScopeTimeout(Exception):
    """作用域超时。父任务超时后抛出，此时所有子任务已被级联取消。"""


# ---------------------------------------------------------------------------
# 全局活动任务统计（用于“取消释放资源”的对比数据）
# ---------------------------------------------------------------------------

_ACTIVE: set = set()


def active_count() -> int:
    """当前仍存活的、由 Scope 托管的子任务总数（含正在收尾的）。"""
    return len(_ACTIVE)


class TaskState:
    """单个托管子任务的运行状态（供断言/观测）。"""

    __slots__ = ("name", "task", "done", "cancel_signalled", "error")

    def __init__(self, name: str) -> None:
        self.name = name
        self.task: Optional[asyncio.Task] = None
        self.done = False
        self.cancel_signalled = False  # 是否收到过取消信号
        self.error: Optional[BaseException] = None


# ---------------------------------------------------------------------------
# 作用域
# ---------------------------------------------------------------------------

class Scope:
    """结构化并发作用域：托管子任务，负责超时与级联取消。

    用法::

        async with Scope(timeout=5.0, clock=RealClock()) as scope:
            scope.spawn(worker("a"))
            scope.spawn(worker("b"))
        # 退出时：所有子任务已结束；超时抛 ScopeTimeout，失败按策略抛出
    """

    def __init__(
        self,
        timeout: Optional[float] = None,
        *,
        clock: Optional[Clock] = None,
        policy: FailurePolicy = FailurePolicy.FAIL_FAST,
        name: str = "scope",
    ) -> None:
        self.timeout = timeout
        self.clock = clock if clock is not None else RealClock()
        self.policy = policy
        self.name = name
        self._children: List[TaskState] = []
        self._errors: List[BaseException] = []
        self._closed = False
        self._timed_out = False
        self._cancelling = False
        self._all_done: Optional[asyncio.Future] = None
        self._watchdog: Optional[asyncio.Task] = None

    # -- 生命周期 ----------------------------------------------------------

    async def __aenter__(self) -> "Scope":
        loop = asyncio.get_running_loop()
        self._all_done = loop.create_future()
        self._all_done.set_result(None)  # 尚无子任务，视为“全部完成”
        if self.timeout is not None:
            self._watchdog = asyncio.ensure_future(self._watch_timeout())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._closed = True
        if self._watchdog is not None:
            self._watchdog.cancel()
        # 父体自身被取消：级联取消子任务，并如实向上传播 CancelledError
        if exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
            self._cancel_all()
            await self._shutdown()
            return False
        # 父体抛其它异常：同样级联取消，但原异常优先传播
        if exc is not None:
            self._cancel_all()
            await self._shutdown()
            return False
        await self._shutdown()
        if self._timed_out:
            raise ScopeTimeout(
                f"作用域 {self.name!r} 超过 {self.timeout}s，"
                f"已级联取消全部子任务"
            )
        self._raise_errors()
        return False

    # -- 子任务管理 --------------------------------------------------------

    def spawn(self, coro: Coroutine, *, name: Optional[str] = None) -> asyncio.Task:
        """在作用域内派生一个子任务。作用域关闭后再 spawn 会报错。"""
        if self._closed:
            raise RuntimeError(f"作用域 {self.name!r} 已关闭，不能再派生任务")
        state = TaskState(name or f"task-{len(self._children)}")
        self._children.append(state)
        if self._all_done is not None and self._all_done.done():
            self._all_done = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(self._run(state, coro))
        state.task = task
        task.add_done_callback(lambda t, s=state: self._done(s, t))
        return task

    @property
    def children(self) -> List[TaskState]:
        return list(self._children)

    @property
    def active(self) -> int:
        """本作用域内仍存活的子任务数（含正在收尾的）。"""
        return sum(1 for s in self._children if not s.done)

    @property
    def uncancelled_children(self) -> int:
        """从未收到取消信号的子任务数（级联取消断言用）。"""
        return sum(1 for s in self._children if not s.cancel_signalled)

    async def wait(self) -> None:
        """等待全部子任务结束（不取消它们）。失败/超时仍在退出时抛出。"""
        while True:
            fut = self._all_done
            if fut is None:
                return
            await asyncio.shield(fut)
            if all(s.done for s in self._children):
                return

    def cancel(self) -> None:
        """手动触发级联取消（不等超时/失败）。"""
        self._cancel_all()

    # -- 内部实现 ----------------------------------------------------------

    async def _run(self, state: TaskState, coro: Coroutine) -> Any:
        _ACTIVE.add(state)
        try:
            return await coro
        except asyncio.CancelledError:
            state.cancel_signalled = True
            raise
        finally:
            _ACTIVE.discard(state)

    def _done(self, state: TaskState, task: asyncio.Task) -> None:
        state.done = True
        if not task.cancelled():
            err = task.exception()
            if err is not None:
                state.error = err
                self._errors.append(err)
                # FAIL_FAST：子任务失败 → 立即级联取消其余子任务
                if (self.policy is FailurePolicy.FAIL_FAST
                        and not self._cancelling and not self._timed_out):
                    self._cancel_all()
        # 无论完成/失败/被取消，都要推进“全部结束”信号，否则 wait() 会挂死
        if all(s.done for s in self._children):
            fut = self._all_done
            if fut is not None and not fut.done():
                fut.set_result(None)

    async def _watch_timeout(self) -> None:
        try:
            await self.clock.sleep(max(0.0, float(self.timeout)))
        except asyncio.CancelledError:
            return  # 作用域正常退出，看门狗被回收
        self._timed_out = True
        self._cancel_all()

    def _cancel_all(self) -> None:
        self._cancelling = True
        for s in self._children:
            if not s.done and s.task is not None:
                s.cancel_signalled = True  # 信号已发出（即使任务吞掉异常）
                s.task.cancel()

    async def _shutdown(self) -> None:
        """等待所有子任务彻底结束（含收尾），保证退出后无残留。"""
        pending = [s.task for s in self._children
                   if s.task is not None and not s.task.done()]
        if pending:
            await asyncio.wait(pending)

    def _raise_errors(self) -> None:
        if not self._errors:
            return
        if len(self._errors) == 1:
            raise self._errors[0]
        raise ExceptionGroup(  # Python 3.11+
            f"作用域 {self.name!r} 内多个子任务失败", self._errors
        )
