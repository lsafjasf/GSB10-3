"""refcount.py -- 引用计数 + Epoch 延迟回收（仅依赖标准库）

核心模型
========
* SharedObject : 被共享的数据。每个对象自带一把锁，acquire/release 只争用
                 本对象的锁，不存在全局大锁。
* Handle       : acquire() 返回的一次性句柄。同一个句柄 release 两次是
                 "重复释放"；release 之后再 get() 是 "释放后继续使用"，
                 两类错误都能被精确捕获。
* ReclaimManager: 读端临界区（reader()）+ Epoch 记账。计数归零时对象不立即
                 销毁，而是带当前 epoch 戳进入待回收队列；只有当"退休时刻
                 可能看到它的所有读者"全部退出临界区后，reclaim() 才会
                 真正执行析构回调。

错误体系
========
* RefCountOverflowError  获取次数超过 max_refcount（计数溢出）
* DoubleReleaseError     同一句柄 release 两次 / 余额变负
* UseAfterReleaseError   对已释放句柄取对象，或 peek 退休对象
* ObjectRetiredError     对已进入待回收队列的对象再次 acquire
* ReaderSectionError     在 reader() 临界区之外调用 peek()
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Deque, List, Optional, Tuple

DEFAULT_MAX_REFCOUNT = (1 << 31) - 1  # 与 32 位有符号整数的上限保持一致


# --------------------------------------------------------------------------- #
# 错误类型
# --------------------------------------------------------------------------- #
class ReclamationError(Exception):
    """本库所有错误的基类。"""


class RefCountOverflowError(ReclamationError, OverflowError):
    """引用计数超过允许的最大值。"""


class DoubleReleaseError(ReclamationError):
    """同一句柄被释放两次，或释放导致计数变为负数。"""


class UseAfterReleaseError(ReclamationError):
    """句柄已释放（或对象已被销毁）后仍然使用。"""


class ObjectRetiredError(UseAfterReleaseError):
    """对象计数已归零、进入待回收队列，不允许再被获取。"""


class ReaderSectionError(ReclamationError):
    """在 reader() 临界区之外对共享对象做了免计数的 peek()。"""


# --------------------------------------------------------------------------- #
# Epoch 记账
# --------------------------------------------------------------------------- #
class _EpochTracker:
    """记录每个读端临界区进入时所处的 epoch。

    对象在 epoch E 退休时，只要还存在一个进入于 epoch <= E 的活跃读者，
    该读者就可能"看见"旧对象，因此不能回收。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._epoch = 0
        self._active: List[int] = []   # 活跃读者的进入 epoch
        self._depth = threading.local()

    def enter(self) -> int:
        with self._lock:
            self._epoch += 1
            self._active.append(self._epoch)
            return self._epoch

    def exit(self, epoch: int) -> None:
        with self._lock:
            self._active.remove(epoch)

    def oldest_active(self) -> Optional[int]:
        with self._lock:
            return min(self._active) if self._active else None

    def current(self) -> int:
        with self._lock:
            return self._epoch

    def is_read_active(self) -> bool:
        return getattr(self._depth, "value", 0) > 0

    @contextmanager
    def section(self):
        depth = getattr(self._depth, "value", 0)
        if depth == 0:
            epoch = self.enter()
        else:
            epoch = None  # 可重入：复用外层临界区
        self._depth.value = depth + 1
        try:
            yield
        finally:
            self._depth.value -= 1
            if epoch is not None:
                self.exit(epoch)


# --------------------------------------------------------------------------- #
# 句柄
# --------------------------------------------------------------------------- #
class Handle:
    """acquire() 的返回值；表示持有一份引用。

    一个 Handle 恰好对应一次 acquire，因此 release 是幂等检测点：
    第二次 release 直接抛 DoubleReleaseError。
    """

    __slots__ = ("_obj", "_released", "_lock")

    def __init__(self, obj: "SharedObject") -> None:
        self._obj = obj
        self._released = False
        self._lock = threading.Lock()

    def get(self) -> Any:
        """取出被保护的对象；句柄释放后再取属于释放后使用。"""
        if self._released:
            raise UseAfterReleaseError(
                "handle already released; cannot dereference (use-after-release)"
            )
        value = self._obj.value
        if self._obj.destroyed:
            raise UseAfterReleaseError(
                "underlying object has already been destroyed"
            )
        return value

    def release(self) -> None:
        with self._lock:
            if self._released:
                raise DoubleReleaseError(
                    "handle released twice (double release)"
                )
            self._released = True
        self._obj._dec()

    # 支持 `with manager.create(v) as h:`
    def __enter__(self) -> "Handle":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


# --------------------------------------------------------------------------- #
# 共享对象
# --------------------------------------------------------------------------- #
class SharedObject:
    def __init__(
        self,
        value: Any,
        manager: "ReclaimManager",
        max_refcount: int = DEFAULT_MAX_REFCOUNT,
        on_destroy: Optional[Callable[[Any], None]] = None,
    ) -> None:
        self.value = value
        self._manager = manager
        self._max_refcount = max_refcount
        self._on_destroy = on_destroy
        self._count = 0
        self._retired = False
        self._destroyed = False
        self._lock = threading.Lock()  # 每对象细粒度锁

    @property
    def refcount(self) -> int:
        return self._count

    @property
    def retired(self) -> bool:
        """计数已归零、在待回收队列中等待（尚未析构）。"""
        return self._retired

    @property
    def destroyed(self) -> bool:
        """析构回调已执行。"""
        return self._destroyed

    def acquire(self) -> Handle:
        with self._lock:
            if self._retired:
                raise ObjectRetiredError(
                    "object is retired (refcount reached zero); cannot acquire"
                )
            if self._count >= self._max_refcount:
                raise RefCountOverflowError(
                    f"refcount overflow: limit is {self._max_refcount}"
                )
            self._count += 1
        return Handle(self)

    def _dec(self) -> None:
        with self._lock:
            if self._count <= 0:
                # 正常路径不可达：Handle.release 已拦截重复释放。
                raise DoubleReleaseError("refcount would become negative")
            self._count -= 1
            if self._count == 0:
                # 只入队，绝不在释放路径上执行任何析构动作。
                self._retired = True
                self._manager.retire(self)

    def peek(self) -> Any:
        """免计数读取，只允许在 manager.reader() 临界区内使用。

        语义保证：只要调用线程处于 reader 临界区，对象的析构回调
        （on_destroy）一定不会在临界区期间执行。
        """
        if not self._manager.is_reader_active():
            raise ReaderSectionError(
                "peek() outside of reader() critical section; "
                "use acquire()/release() instead"
            )
        if self._destroyed:
            raise UseAfterReleaseError(
                "underlying object has already been destroyed"
            )
        return self.value

    def _destroy(self) -> None:
        if self._on_destroy is not None:
            self._on_destroy(self.value)
        self._destroyed = True
        self.value = None  # 主动断开引用，辅助 GC 并暴露悬空使用


# --------------------------------------------------------------------------- #
# 回收管理器
# --------------------------------------------------------------------------- #
class ReclaimManager:
    """对象的工厂 + 待回收队列 + Epoch 判定。"""

    def __init__(self, max_refcount: int = DEFAULT_MAX_REFCOUNT) -> None:
        self._max_refcount = max_refcount
        self._tracker = _EpochTracker()
        self._pending: Deque[Tuple[int, SharedObject]] = deque()
        self._qlock = threading.Lock()
        self._reclaimer: Optional["_BackgroundReclaimer"] = None

    # -- 对象工厂 ----------------------------------------------------------- #
    def create(
        self,
        value: Any,
        on_destroy: Optional[Callable[[Any], None]] = None,
        max_refcount: Optional[int] = None,
    ) -> Handle:
        """创建对象并返回首引用句柄（初始计数为 1）。"""
        obj = SharedObject(
            value,
            self,
            max_refcount=self._max_refcount if max_refcount is None else max_refcount,
            on_destroy=on_destroy,
        )
        return obj.acquire()

    # -- 读端临界区 --------------------------------------------------------- #
    @contextmanager
    def reader(self):
        """声明一个读端临界区；临界区内对象不会被真正回收。

        可重入（同一线程可嵌套），嵌套时退出内层不会结束保护。
        """
        with self._tracker.section():
            yield

    def is_reader_active(self) -> bool:
        return self._tracker.is_read_active()

    # -- 队列（由 SharedObject 调用）---------------------------------------- #
    def retire(self, obj: SharedObject) -> int:
        with self._qlock:
            stamp = self._tracker.current()
            self._pending.append((stamp, obj))
            return stamp

    @property
    def pending_count(self) -> int:
        with self._qlock:
            return len(self._pending)

    # -- 回收 --------------------------------------------------------------- #
    def reclaim(self) -> int:
        """回收所有安全对象，返回本次实际析构的数量。

        判定规则：对象在 epoch E 退休；记最老活跃读者的 epoch 为 M。
        若没有活跃读者，或 E < M（退休之后才进入的读者不可能看见它），
        则对象安全。析构回调在队列锁之外执行，避免回调与获取/释放互相阻塞。
        """
        with self._qlock:
            oldest = self._tracker.oldest_active()
            safe: List[SharedObject] = []
            kept: Deque[Tuple[int, SharedObject]] = deque()
            for stamp, obj in self._pending:
                if oldest is None or stamp < oldest:
                    safe.append(obj)
                else:
                    kept.append((stamp, obj))
            self._pending = kept
        first_error: Optional[BaseException] = None
        for obj in safe:
            try:
                obj._destroy()
            except BaseException as exc:  # 析构失败不拖累队列中其余对象
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
        return len(safe)

    # -- 后台回收线程（可选）------------------------------------------------ #
    def start_background_reclaimer(self, interval: float = 0.01) -> None:
        if self._reclaimer is not None:
            return
        self._reclaimer = _BackgroundReclaimer(self, interval)
        self._reclaimer.start()

    def stop_background_reclaimer(self, final_reclaim: bool = True) -> None:
        if self._reclaimer is None:
            return
        reclaimer = self._reclaimer
        self._reclaimer = None
        reclaimer.stop()
        if final_reclaim:
            self.reclaim()


class _BackgroundReclaimer(threading.Thread):
    """周期性调用 reclaim() 的辅助线程。"""

    def __init__(self, manager: ReclaimManager, interval: float) -> None:
        super().__init__(daemon=True, name="reclaimer")
        self._manager = manager
        self._interval = interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=2.0)

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            self._manager.reclaim()
