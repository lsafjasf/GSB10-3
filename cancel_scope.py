"""取消作用域（CancelScope）：结构化并发下的资源清理。

仅使用标准库（asyncio）。语义：

- ``scope.cancel()`` 取消作用域，并向下传播到所有子作用域；
- 父作用域退出前，先取消并等待全部子作用域清理完成；
- 清理函数（``defer`` 登记）按与登记相反的顺序（LIFO）执行；
- 清理函数抛错不会中断后续清理，所有错误收集后作为一个
  ``ExceptionGroup`` 一起抛出；
- 子任务（``start_soon``）中的清理错误/异常也会汇总进父作用域的报告。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Optional

CleanupFn = Callable[[], Any]  # 同步或返回 awaitable 均可


class CancelScope:
    """异步取消作用域，用作 ``async with`` 上下文管理器。"""

    def __init__(self, parent: Optional["CancelScope"] = None) -> None:
        self._parent = parent
        self._cleanups: list[CleanupFn] = []
        self._children: set[CancelScope] = set()
        self._finished: set[CancelScope] = set()
        self._cancel_requested = False
        self._task: Optional[asyncio.Task] = None
        self._done = asyncio.Event()
        self._entered = False
        #: 子任务（start_soon）运行期间未被取消语义吸收的异常，供父作用域汇总。
        self.error: Optional[BaseException] = None

    # ------------------------------------------------------------------ API

    def defer(self, fn: CleanupFn) -> CleanupFn:
        """登记清理函数；退出作用域时按登记的相反顺序执行。"""
        self._cleanups.append(fn)
        return fn

    async def use(
        self,
        acquire: Callable[[], Any],
        release: Callable[[Any], Any],
    ) -> Any:
        """申请资源并登记对应的释放函数，返回资源对象。

        释放函数与申请一一对应，随作用域退出按逆序执行。
        """
        resource = acquire()
        if inspect.isawaitable(resource):
            resource = await resource
        self.defer(lambda: release(resource))
        return resource

    def child(self) -> "CancelScope":
        """创建一个子作用域（取消会向下传播到它）。"""
        return CancelScope(parent=self)

    def start_soon(self, fn: Callable[..., Awaitable[Any]], *args: Any) -> asyncio.Task:
        """在子作用域里启动一个子任务。

        子任务签名为 ``fn(child_scope, *args)``。父作用域取消时会取消该
        子任务，并等待其清理完成；子任务内的清理错误会汇总进父作用域。
        """
        child = self.child()

        async def runner() -> None:
            try:
                async with child:
                    await fn(child, *args)
            except asyncio.CancelledError:
                pass  # 取消是正常路径，清理已在 child.__aexit__ 完成
            except BaseException as exc:  # 含清理错误的 ExceptionGroup
                child.error = exc

        return asyncio.create_task(runner())

    def cancel(self) -> None:
        """请求取消：向下传播到所有子作用域，并打断本作用域所在任务。"""
        if self._cancel_requested:
            return  # 幂等：重复取消不重复打断
        self._cancel_requested = True
        for child in list(self._children):
            child.cancel()
        if self._task is not None:
            self._task.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancel_requested

    # ------------------------------------------------------- context manager

    async def __aenter__(self) -> "CancelScope":
        if self._entered:
            raise RuntimeError("CancelScope 不能重复进入")
        self._entered = True
        self._task = asyncio.current_task()
        if self._parent is not None:
            self._parent._adopt(self)
        if self._cancel_requested and self._task is not None:
            # 进入前已被取消：立即打断，保证清理路径被走一遍
            self._task.cancel()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        errors: list[BaseException] = []

        # 1. 取消并等待所有子作用域清理完成（取消向下传播，先子后父）。
        for child in list(self._children):
            child.cancel()
        await self._wait_children(errors)

        # 2. 按与申请相反的顺序执行清理；单个清理抛错不中断后续清理。
        while self._cleanups:
            fn = self._cleanups.pop()
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
            except BaseException as err:  # 含清理期间再次到来的 CancelledError
                errors.append(err)

        # 3. 从父作用域摘出并标记完成（让父的等待结束）。
        if self._parent is not None:
            self._parent._children.discard(self)
            self._parent._finished.add(self)
        self._done.set()

        if errors:
            group_cls = (
                ExceptionGroup
                if all(isinstance(e, Exception) for e in errors)
                else BaseExceptionGroup
            )
            raise group_cls("取消作用域清理期间发生错误", errors)
        return False  # 不压制原异常（如 CancelledError）

    # ------------------------------------------------------------- internal

    def _adopt(self, child: "CancelScope") -> None:
        self._children.add(child)
        if self._cancel_requested:
            child.cancel()  # 父已取消：新子作用域立即取消

    async def _wait_children(self, errors: list[BaseException]) -> None:
        """等待全部子作用域完成清理，并汇总子任务上报的错误。"""
        watched = self._children | self._finished
        while self._children:
            try:
                await asyncio.gather(*(c._done.wait() for c in list(self._children)))
            except asyncio.CancelledError as err:
                # 等待期间再次被打断：记录但继续等，子作用域必须收干净。
                errors.append(err)
                for child in list(self._children):
                    child.cancel()
        for child in watched:  # 含在父进入 __aexit__ 前就已清理完的子作用域
            if child.error is not None:
                errors.append(child.error)
        self._finished.clear()
