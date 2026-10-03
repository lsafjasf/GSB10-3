"""取消作用域（cancellation scope）。

- cancel() 向下传播到所有子作用域
- 父作用域退出前等待全部子作用域完成清理
- 清理函数按注册（申请）的逆序执行，且子作用域先于父作用域自身的资源
- 单个清理函数抛错不中断后续清理，错误汇总为 CleanupError 一起抛出

仅依赖标准库 asyncio。
"""

from __future__ import annotations

import asyncio
import inspect


class ScopeCancelled(asyncio.CancelledError):
    """作用域被取消时，向作用域体内等待中的代码抛出的异常。"""


class CleanupError(Exception):
    """一个或多个清理函数抛错时的汇总异常。"""

    def __init__(self, errors):
        self.errors = list(errors)
        detail = "; ".join("%s: %r" % (type(e).__name__, e) for e in self.errors)
        super().__init__("%d cleanup callback(s) failed -> %s" % (len(self.errors), detail))


class CancelScope:
    """异步取消作用域，配合 ``async with`` 使用。"""

    def __init__(self, parent: "CancelScope | None" = None):
        self._parent = parent
        self._children: set[CancelScope] = set()
        self._cleanups: list = []
        self._cancel_requested = False
        self._entered = False
        self._exited = False
        self._host_task: asyncio.Task | None = None
        self._all_children_done = asyncio.Event()
        self._all_children_done.set()
        if parent is not None:
            parent._adopt(self)

    # ---- 状态查询 ----------------------------------------------------------

    @property
    def cancelled(self) -> bool:
        return self._cancel_requested

    def child(self) -> "CancelScope":
        """在当前作用域下创建子作用域（也可直接 CancelScope(parent=scope)）。"""
        return CancelScope(parent=self)

    # ---- 资源登记 ----------------------------------------------------------

    def on_cleanup(self, fn) -> None:
        """登记一个清理函数，可以是普通函数或协程函数。

        退出时按登记的逆序调用。
        """
        if self._exited:
            raise RuntimeError("cannot register cleanup on an exited scope")
        self._cleanups.append(fn)

    # ---- 取消传播 ----------------------------------------------------------

    def cancel(self) -> None:
        """请求取消本作用域：标记、取消宿主任务、向下传播给子作用域。"""
        if self._exited:
            return
        self._cancel_requested = True
        for child in list(self._children):
            child.cancel()
        if self._host_task is not None and not self._host_task.done():
            self._host_task.cancel()

    async def checkpoint(self) -> None:
        """协作式检查点：作用域已被取消时抛 ScopeCancelled。"""
        if self._cancel_requested:
            raise ScopeCancelled("scope was cancelled")
        await asyncio.sleep(0)
        if self._cancel_requested:
            raise ScopeCancelled("scope was cancelled")

    # ---- 生命周期 ----------------------------------------------------------

    async def __aenter__(self) -> "CancelScope":
        if self._entered:
            raise RuntimeError("a CancelScope cannot be entered twice")
        self._entered = True
        self._host_task = asyncio.current_task()
        if self._cancel_requested:
            # 进入即失败：先从父作用域注销，否则父作用域会永远等不到我们退出
            self._exited = True
            if self._parent is not None:
                self._parent._child_exited(self)
            raise ScopeCancelled("scope was cancelled before entry")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._exited = True

        # 处理本作用域 cancel() 对宿主任务发出的那次取消请求：
        # - 已投递：作为 exc 传进来（见下方 suppress_cancel），只需 uncancel 记账；
        # - 未投递（body 里之后没有 await 点）：主动让出一次执行把它消费掉，
        #   否则它会在清理过程中或任务结束时“迟到”冒出来。外部取消不受影响。
        task = asyncio.current_task()
        if self._cancel_requested and self._host_task is task:
            delivered = (
                exc_type is not None
                and issubclass(exc_type, asyncio.CancelledError)
            )
            if not delivered:
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    pass
            uncancel = getattr(task, "uncancel", None)
            if uncancel is not None and task.cancelling() > 0:
                uncancel()

        # 仅吞掉“由本作用域 cancel() 主动取消宿主任务”造成的 CancelledError；
        # 外部对任务的取消不受影响，继续向上传播。
        suppress_cancel = (
            exc_type is not None
            and issubclass(exc_type, asyncio.CancelledError)
            and self._cancel_requested
        )

        # 1) 先等所有子作用域完成清理（子资源后申请、先释放）。
        #    兜底：自己取消的投递若仍在此处到达（如低版本无 uncancel），
        #    吸收掉，保证清理流程不被打断。
        while True:
            try:
                await self._all_children_done.wait()
                break
            except asyncio.CancelledError:
                if not self._cancel_requested:
                    raise

        # 2) 再按与申请相反的顺序执行本作用域的清理函数
        errors = []
        while self._cleanups:
            fn = self._cleanups.pop()
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError as e:
                if self._cancel_requested:
                    # 自己取消的投递恰好落在清理的 await 点上：吸收，继续清理
                    pass
                else:
                    errors.append(e)
            except Exception as e:
                errors.append(e)

        # 3) 从父作用域注销
        if self._parent is not None:
            self._parent._child_exited(self)

        # 4) 错误汇总：清理抛错不得互相遮蔽
        if errors:
            cleanup_error = CleanupError(errors)
            if exc is not None and not suppress_cancel:
                raise cleanup_error from exc
            raise cleanup_error

        return suppress_cancel

    # ---- 父子关系 ----------------------------------------------------------

    def _adopt(self, child: "CancelScope") -> None:
        if self._exited:
            raise RuntimeError("cannot add a child to an exited scope")
        self._children.add(child)
        self._all_children_done.clear()
        if self._cancel_requested:
            child.cancel()

    def _child_exited(self, child: "CancelScope") -> None:
        self._children.discard(child)
        if not self._children:
            self._all_children_done.set()
