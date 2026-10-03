"""引用计数 + 宽限期延迟回收的核心实现。

模型（RCU 风格）：

- 每个共享对象带一把私有锁，引用计数的获取/释放在锁内完成，天然原子。
- 计数归零时对象进入 RETIRED 状态并被放入所属 Domain 的待回收队列，
  绝不就地销毁。
- Domain 用单调递增的「进入序号 / 退出序号」记录读方进出。对象入队时
  快照当时的进入序号；只有当退出序号追上该快照（即对象入队前已进入的
  所有读方全部退出）后，对象才真正被回收。
- 晚于入队时刻进入的读方不可能再看到该对象（注册表已摘除、acquire 被
  拒绝），因此无需等待。

锁序约定（避免死锁）：先取对象锁，再取 Domain 锁；回收回调在
Domain 锁外执行。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Deque, Iterator, Optional, Tuple

DEFAULT_MAX_REFCOUNT = 1 << 30

_ALIVE = "ALIVE"
_RETIRED = "RETIRED"
_RECLAIMED = "RECLAIMED"


class LifecycleError(RuntimeError):
    """所有生命周期违规的基类。"""


class RefcountOverflowError(LifecycleError):
    """引用计数超过 max_refcount。"""


class DoubleReleaseError(LifecycleError):
    """重复释放：同一个引用被释放两次，或出现无配对的释放。"""


class UseAfterReleaseError(LifecycleError):
    """释放后继续使用：通过已释放的引用访问数据。"""


class ObjectReclaimedError(UseAfterReleaseError):
    """对象已被回收后仍被访问。"""


class RetiredObjectError(LifecycleError):
    """对已退役（计数已归零、等待回收）的对象发起新的获取。"""


class DrainTimeoutError(TimeoutError):
    """等待回收完成超时（通常是有读方迟迟不退出）。"""


class Ref:
    """一次 acquire 得到的引用句柄。

    - release() 把引用计数减一；对同一个 Ref 再次 release 抛
      DoubleReleaseError。
    - 通过 Ref 访问数据（.value / .get()）在 release 之后抛
      UseAfterReleaseError。
    - 支持 with 语句。
    """

    __slots__ = ("_obj", "_released")

    def __init__(self, obj: "SharedObject") -> None:
        self._obj = obj
        self._released = False

    @property
    def obj(self) -> "SharedObject":
        return self._obj

    @property
    def released(self) -> bool:
        return self._released

    @property
    def value(self) -> Any:
        return self.get()

    def get(self) -> Any:
        if self._released:
            raise UseAfterReleaseError(
                "reference already released; cannot access payload"
            )
        return self._obj._payload_for(self)

    def release(self) -> None:
        self._obj._release_ref(self)

    def __enter__(self) -> "Ref":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        state = "released" if self._released else "held"
        return f"<Ref {state} obj={self._obj!r}>"


class SharedObject:
    """带引用计数的共享对象。

    创建时自带一个「创建引用」（计数为 1），用 release() 释放；
    之后每次 acquire() 计数加一并返回 Ref，Ref.release() 计数减一。
    """

    __slots__ = (
        "_domain",
        "_payload",
        "_reclaim_cb",
        "_max_refcount",
        "_lock",
        "_refcount",
        "_state",
        "_creation_ref_held",
        "_retired_at",
        "_grace_snapshot",
        "_reclaimed_at",
        "_reclaimed_event",
    )

    def __init__(
        self,
        domain: "Domain",
        payload: Any,
        reclaim: Optional[Callable[["SharedObject"], None]] = None,
        max_refcount: int = DEFAULT_MAX_REFCOUNT,
    ) -> None:
        if max_refcount < 1:
            raise ValueError("max_refcount must be >= 1")
        self._domain = domain
        self._payload = payload
        self._reclaim_cb = reclaim
        self._max_refcount = max_refcount
        self._lock = threading.Lock()
        self._refcount = 1
        self._state = _ALIVE
        self._creation_ref_held = True
        self._retired_at: Optional[float] = None
        self._grace_snapshot: Optional[int] = None
        self._reclaimed_at: Optional[float] = None
        self._reclaimed_event = threading.Event()

    # ---- 只读状态 -------------------------------------------------

    @property
    def refcount(self) -> int:
        with self._lock:
            return self._refcount

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def is_retired(self) -> bool:
        return self.state == _RETIRED

    @property
    def is_reclaimed(self) -> bool:
        return self.state == _RECLAIMED

    @property
    def retired_at(self) -> Optional[float]:
        return self._retired_at

    @property
    def reclaimed_at(self) -> Optional[float]:
        return self._reclaimed_at

    @property
    def grace_snapshot(self) -> Optional[int]:
        """入队时快照的读方进入序号（调试用）。"""
        return self._grace_snapshot

    # ---- 引用计数 -------------------------------------------------

    def acquire(self) -> Ref:
        """计数加一，返回 Ref。溢出或对象已退役时报错。"""
        with self._lock:
            if self._state != _ALIVE:
                raise RetiredObjectError(
                    f"cannot acquire object in state {self._state}"
                )
            if self._refcount >= self._max_refcount:
                raise RefcountOverflowError(
                    f"refcount {self._refcount} would exceed "
                    f"max_refcount {self._max_refcount}"
                )
            self._refcount += 1
            return Ref(self)

    def release(self) -> None:
        """释放创建引用。重复释放抛 DoubleReleaseError。"""
        with self._lock:
            if not self._creation_ref_held:
                raise DoubleReleaseError("creation reference already released")
            self._creation_ref_held = False
            self._decrement_locked()

    def _release_ref(self, ref: Ref) -> None:
        with self._lock:
            if ref._released:
                raise DoubleReleaseError("this Ref was already released")
            ref._released = True
            self._decrement_locked()

    def _decrement_locked(self) -> None:
        """调用方必须持有 self._lock。"""
        if self._state == _RECLAIMED:
            raise DoubleReleaseError("release after object was reclaimed")
        if self._refcount <= 0:
            raise DoubleReleaseError("unbalanced release: refcount already 0")
        self._refcount -= 1
        if self._refcount == 0:
            self._state = _RETIRED
            self._retired_at = time.monotonic()
            # 只入队，不销毁；真正的回收由 Domain 在宽限期后执行。
            self._grace_snapshot = self._domain._enqueue_retired(self)

    # ---- 数据访问 -------------------------------------------------

    def _payload_for(self, ref: Ref) -> Any:
        with self._lock:
            if self._state == _RECLAIMED:
                raise ObjectReclaimedError("object has been reclaimed")
            return self._payload

    @property
    def payload(self) -> Any:
        """读方在读临界区内使用；对象已回收则抛 ObjectReclaimedError。"""
        with self._lock:
            if self._state == _RECLAIMED:
                raise ObjectReclaimedError("object has been reclaimed")
            return self._payload

    # ---- 回收 -------------------------------------------------

    def _reclaim(self) -> None:
        """由 Domain 在宽限期结束后调用。"""
        with self._lock:
            if self._state != _RETIRED:
                raise LifecycleError(
                    f"reclaim called on object in state {self._state}"
                )
            self._state = _RECLAIMED
            self._reclaimed_at = time.monotonic()
            self._payload = None
        if self._reclaim_cb is not None:
            self._reclaim_cb(self)
        self._reclaimed_event.set()

    def wait_reclaimed(self, timeout: Optional[float] = None) -> bool:
        return self._reclaimed_event.wait(timeout)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<SharedObject state={self.state} refcount={self.refcount}>"
        )


class Domain:
    """回收域：维护读方宽限期与待回收队列。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._enter_seq = 0
        self._exit_seq = 0
        self._readers_active = 0
        self._retired: Deque[Tuple[int, SharedObject]] = deque()
        self._local = threading.local()

    # ---- 读侧 -------------------------------------------------

    def reader_enter(self) -> None:
        depth = getattr(self._local, "depth", 0)
        self._local.depth = depth + 1
        if depth == 0:
            with self._cond:
                self._enter_seq += 1
                self._readers_active += 1

    def reader_exit(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth <= 0:
            raise LifecycleError("reader_exit without matching reader_enter")
        self._local.depth = depth - 1
        if depth == 1:
            with self._cond:
                self._exit_seq += 1
                self._readers_active -= 1
                self._cond.notify_all()

    @contextmanager
    def read_lock(self) -> Iterator[None]:
        self.reader_enter()
        try:
            yield
        finally:
            self.reader_exit()

    def in_read_section(self) -> bool:
        return getattr(self._local, "depth", 0) > 0

    @property
    def readers_active(self) -> int:
        with self._lock:
            return self._readers_active

    # ---- 回收侧 -------------------------------------------------

    def register(
        self,
        payload: Any,
        reclaim: Optional[Callable[[SharedObject], None]] = None,
        max_refcount: int = DEFAULT_MAX_REFCOUNT,
    ) -> SharedObject:
        return SharedObject(self, payload, reclaim, max_refcount)

    def _enqueue_retired(self, obj: SharedObject) -> int:
        """对象计数归零时调用（调用方持有对象锁）。返回宽限期快照。"""
        with self._cond:
            snapshot = self._enter_seq
            self._retired.append((snapshot, obj))
            self._cond.notify_all()
            return snapshot

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._retired)

    def grace_reached(self, snapshot: int) -> bool:
        """快照之前进入的读方是否已全部退出。"""
        with self._lock:
            return self._exit_seq >= snapshot

    def reclaim_ready(self) -> int:
        """回收队首起所有已过宽限期的对象，返回回收数量（FIFO）。"""
        ready = []
        with self._cond:
            exit_seq = self._exit_seq
            while self._retired and self._retired[0][0] <= exit_seq:
                ready.append(self._retired.popleft()[1])
        for obj in ready:
            obj._reclaim()
        return len(ready)

    def drain(self, timeout: Optional[float] = None) -> int:
        """阻塞直到待回收队列清空并完成回收。超时抛 DrainTimeoutError。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        total = 0
        while True:
            total += self.reclaim_ready()
            with self._cond:
                if not self._retired:
                    return total
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    raise DrainTimeoutError(
                        f"{len(self._retired)} object(s) still waiting for "
                        "readers to exit"
                    )
                self._cond.wait(remaining)

    def wait_grace(self, snapshot: int, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._exit_seq < snapshot:
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True


class ReclaimDomain(Domain):
    """带后台回收线程的 Domain。对象一过宽限期即被回收。"""

    def __init__(self) -> None:
        super().__init__()
        self._worker_stop = threading.Event()
        self._worker = threading.Thread(
            target=self._reaper_loop, name="rcuref-reaper", daemon=True
        )
        self._worker.start()

    def _reaper_loop(self) -> None:
        while not self._worker_stop.is_set():
            self.reclaim_ready()
            with self._cond:
                self._cond.wait(timeout=0.005)

    def stop(self, timeout: Optional[float] = None) -> None:
        self._worker_stop.set()
        with self._cond:
            self._cond.notify_all()
        self._worker.join(timeout)

    def __enter__(self) -> "ReclaimDomain":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()
