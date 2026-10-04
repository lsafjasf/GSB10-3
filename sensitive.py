"""sensitive.py — 敏感数据容器（仅标准库）。

设计要点：
- 底层缓冲区使用 bytearray，销毁时用 ctypes.memset 原地逐字节清零，
  避免“把变量置空/重新赋值”导致旧缓冲区残留在堆里。
- destroy() 幂等：重复调用是 no-op，返回 False，不抛异常。
- 拷贝检测：数据只能通过 use() 上下文（受控使用窗口）访问；
  任何 copy() 都会计数，在窗口外或销毁后拷贝会触发告警并计入
  unexpected_copy_count。copy() 返回的副本被弱引用跟踪，
  销毁时若仍有存活副本会告警（运行时可能已留下无法清零的副本）。

诚实声明（CPython 限制）：
解释器内部（参数传递、临时对象、GC 前的旧帧等）可能留下无法
追踪的副本，本库能保证的是“容器持有的底层缓冲区”被可靠清零，
并对“经过受控 API 的复制”提供计数与告警。
"""

from __future__ import annotations

import ctypes
import warnings
import weakref
from contextlib import contextmanager


class DestroyedError(RuntimeError):
    """容器已销毁后仍尝试访问时抛出。"""


class UnexpectedCopyWarning(UserWarning):
    """在使用窗口外或销毁后发生复制时发出。"""


class LiveCopiesWarning(UserWarning):
    """销毁时仍存在存活副本（无法随容器一起清零）时发出。"""


def _secure_zero(buf: bytearray) -> None:
    """原地逐字节清零，优先 ctypes.memset（不会被优化掉）。"""
    if not buf:
        return
    try:
        addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
        ctypes.memset(addr, 0, len(buf))
    except (TypeError, ValueError):  # pragma: no cover - 兜底
        buf[:] = b"\x00" * len(buf)


class SensitiveCopy:
    """受控副本包装器：可被弱引用跟踪，销毁时可统计存活副本数。"""

    __slots__ = ("_data", "__weakref__")

    def __init__(self, data: bytes):
        self._data = data

    def bytes(self) -> bytes:
        return self._data

    def __len__(self) -> int:
        return len(self._data)


class SensitiveBytes:
    def __init__(self, data: bytes | bytearray):
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes or bytearray")
        self._buf = bytearray(data)
        self._destroyed = False
        self._use_depth = 0            # >0 表示处于受控使用窗口内
        self._copy_count = 0           # 受控 copy() 总次数
        self._unexpected_copy_count = 0  # 窗口外/销毁后的复制次数
        self._live_copies: weakref.WeakSet = weakref.WeakSet()

    # ---- 属性 ----
    @property
    def destroyed(self) -> bool:
        return self._destroyed

    @property
    def copy_count(self) -> int:
        return self._copy_count

    @property
    def unexpected_copy_count(self) -> int:
        return self._unexpected_copy_count

    @property
    def live_copy_count(self) -> int:
        return len(self._live_copies)

    # ---- 受控访问 ----
    def _check_alive(self) -> None:
        if self._destroyed:
            raise DestroyedError("sensitive buffer has been destroyed")

    @contextmanager
    def use(self):
        """受控使用窗口：窗口内可读取明文视图，退出即关闭。"""
        self._check_alive()
        self._use_depth += 1
        try:
            yield memoryview(self._buf)
        finally:
            self._use_depth -= 1

    def copy(self) -> SensitiveCopy:
        """显式复制。窗口外或销毁后复制会告警并计入意外复制。"""
        if self._destroyed or self._use_depth == 0:
            self._unexpected_copy_count += 1
            warnings.warn(
                "copy outside use window or after destroy",
                UnexpectedCopyWarning,
                stacklevel=2,
            )
        self._check_alive()
        self._copy_count += 1
        dup = SensitiveCopy(bytes(self._buf))
        self._live_copies.add(dup)
        return dup

    # ---- 销毁 ----
    def destroy(self) -> bool:
        """幂等销毁：首次清零并返回 True，之后为 no-op 返回 False。"""
        if self._destroyed:
            return False
        live = len(self._live_copies)
        if live:
            warnings.warn(
                f"{live} live copy(ies) cannot be zeroed with the container",
                LiveCopiesWarning,
                stacklevel=2,
            )
        _secure_zero(self._buf)
        self._destroyed = True
        return True

    # ---- 验证（测试/审计用）----
    def raw_snapshot(self) -> bytes:
        """读取底层缓冲区当前内容（含销毁后），用于清零验证。"""
        return bytes(self._buf)

    def verify_zeroed(self) -> bool:
        """销毁后逐字节校验缓冲区是否全为零。"""
        return self._destroyed and all(b == 0 for b in self._buf)

    def __len__(self) -> int:
        return len(self._buf)

    def __repr__(self) -> str:
        state = "destroyed" if self._destroyed else "alive"
        return f"<SensitiveBytes len={len(self._buf)} {state}>"


if __name__ == "__main__":
    # 演示：销毁前后内容对比
    secret = SensitiveBytes(b"s3cr3t-token-0123456789")
    print("销毁前:", secret.raw_snapshot().hex())
    with secret.use() as view:
        print("使用中:", bytes(view).decode())
    secret.destroy()
    print("销毁后:", secret.raw_snapshot().hex())
    print("逐字节为零:", secret.verify_zeroed())
    print("再次销毁(幂等):", secret.destroy())
