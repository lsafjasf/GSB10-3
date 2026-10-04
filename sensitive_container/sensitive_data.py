"""敏感数据容器：可验证清零、幂等销毁、意外复制检测（仅标准库）。

设计要点
--------
1. 明文只存放在唯一的 ``bytearray`` 中，访问必须经由 ``access()`` 上下文
   管理器拿到的 ``memoryview``，避免产生 ``bytes``/``str`` 等不可清零副本。
2. ``destroy()`` 把底层缓冲区逐字节覆写为 0，且幂等：重复调用不报错、
   不改变结果。销毁后任何访问抛 ``SecretDestroyedError``。
3. 使用期外的意外复制由 ``LeakScanner`` 检测：遍历 GC 追踪对象及其引用，
   统计除容器自身缓冲区之外仍持有明文字节的对象数量（拷贝计数）。
4. 从 ``access()`` 逃逸出去的 memoryview（会话结束后仍存活）会被
   弱引用跟踪并产生告警。

限制（标准库范围内的固有边界）
------------------------------
- 解释器/运行时内部可能残留副本（如已 ``bytes()``/``str()`` 化的数据、
  被交换到磁盘的页、GC 尚未回收的临时对象）。本库的对策是：全程只用
  可清零的 ``bytearray``/``memoryview``，并提供扫描器把"还能找到的副本"
  暴露出来。``ctypes`` 级别的逐字节覆写属于实现细节，不保证跨解释器有效。
"""

from __future__ import annotations

import gc
import sys
import threading
import types
import warnings
import weakref
from dataclasses import dataclass, field
from typing import Optional, Set

__all__ = [
    "SecretError",
    "SecretDestroyedError",
    "SecretInUseError",
    "SecretBuffer",
    "LeakScanner",
    "LeakFinding",
    "LeakReport",
]


class SecretError(Exception):
    """敏感数据容器相关错误的基类。"""


class SecretDestroyedError(SecretError):
    """缓冲区已销毁，禁止继续访问。"""


class SecretInUseError(SecretError):
    """缓冲区仍在使用（存在活跃会话），拒绝销毁。"""


class SecretBuffer:
    """敏感数据容器。

    用法::

        secret = SecretBuffer(b"top-secret")
        with secret.access() as view:
            ...  # 通过 memoryview 读写，不产生副本
        secret.destroy()
        secret.destroy()  # 幂等，不报错
    """

    __slots__ = ("_buf", "_destroyed", "_active_sessions", "_views", "_lock")

    def __init__(self, data: bytes | bytearray | memoryview | str):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"不支持的数据类型: {type(data)!r}")
        # 唯一的明文存储；构造时复制一份，之后与调用方传入的对象脱钩。
        self._buf: Optional[bytearray] = bytearray(data)
        self._destroyed = False
        self._active_sessions = 0
        # 弱引用跟踪所有签发出去的 memoryview，用于逃逸检测。
        # 注意：可写 memoryview 不可哈希，无法放进 WeakSet，故用列表惰性清理。
        self._views: "list[weakref.ref]" = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 访问
    # ------------------------------------------------------------------
    def access(self) -> "_AccessSession":
        """打开一个访问会话（上下文管理器），返回只读/可写的 memoryview。

        会话内允许通过返回的 memoryview 读写明文；会话结束后若仍有
        memoryview 逃逸存活，会产生告警。
        """
        self._ensure_alive()
        return _AccessSession(self)

    def _checkout(self) -> memoryview:
        with self._lock:
            self._ensure_alive()
            self._active_sessions += 1
            view = memoryview(self._buf)  # type: ignore[arg-type]
            self._views.append(weakref.ref(view))
            return view

    def _checkin(self) -> None:
        with self._lock:
            self._active_sessions -= 1
        escaped = self.escaped_view_count()
        if escaped:
            warnings.warn(
                f"访问会话已结束，但仍有 {escaped} 个 memoryview 逃逸存活，"
                "它们持有明文引用，请尽快释放（del / 关闭）。",
                ResourceWarning,
                stacklevel=3,
            )

    # ------------------------------------------------------------------
    # 销毁
    # ------------------------------------------------------------------
    def destroy(self, *, force: bool = False) -> None:
        """销毁：把底层缓冲区逐字节覆写为 0。幂等。

        - 已销毁状态下重复调用是空操作，不报错、不改变结果。
        - 存在活跃会话时默认拒绝销毁（抛 ``SecretInUseError``），
          传 ``force=True`` 可强制销毁（提前销毁场景）。
        """
        with self._lock:
            if self._destroyed:
                return  # 幂等：重复销毁为空操作
            if self._active_sessions > 0 and not force:
                raise SecretInUseError(
                    f"仍有 {self._active_sessions} 个活跃访问会话，"
                    "请先退出会话，或使用 force=True 强制销毁"
                )
            buf = self._buf
            self._buf = None
            self._destroyed = True
        # 逐字节清零（锁外执行，buf 已与本对象脱钩）。
        # 对 CPython，bytearray 的就地切片赋值会覆写原内存。
        buf[:] = b"\x00" * len(buf)

    @property
    def destroyed(self) -> bool:
        return self._destroyed

    def _ensure_alive(self) -> None:
        if self._destroyed:
            raise SecretDestroyedError("敏感数据已销毁，禁止访问")

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    def escaped_view_count(self) -> int:
        """当前仍存活的、由本容器签发的 memoryview 数量。"""
        self._views[:] = [r for r in self._views if r() is not None]
        return len(self._views)

    def audit(self, *, scan_strings: bool = True) -> "LeakReport":
        """扫描解释器内存，统计本容器明文在使用期外的意外复制。

        容器自身的缓冲区（未销毁时）不计入泄漏。
        """
        if self._destroyed:
            # 已销毁：明文应为全零，扫描全零模式没有意义，直接返回空报告。
            return LeakReport(pattern_length=0)
        scanner = LeakScanner(
            bytes(self._buf),  # type: ignore[arg-type]
            extra_exclude_ids={id(self._buf)},
            scan_strings=scan_strings,
        )
        return scanner.scan()

    # ------------------------------------------------------------------
    # 容器协议：一律拒绝隐式读出明文
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        self._ensure_alive()
        return len(self._buf)  # type: ignore[arg-type]

    def __bytes__(self) -> bytes:
        raise SecretError("禁止隐式复制明文，请使用 access() 上下文")

    def __repr__(self) -> str:
        state = "destroyed" if self._destroyed else f"{len(self._buf)} bytes"  # type: ignore[arg-type]
        return f"<SecretBuffer {state}>"

    def __eq__(self, other) -> bool:  # 避免意外比较泄露
        raise SecretError("SecretBuffer 不支持比较")

    __hash__ = None  # type: ignore[assignment]


class _AccessSession:
    """``SecretBuffer.access()`` 返回的上下文管理器。"""

    __slots__ = ("_owner", "_view")

    def __init__(self, owner: SecretBuffer):
        self._owner = owner
        self._view: Optional[memoryview] = None

    def __enter__(self) -> memoryview:
        self._view = self._owner._checkout()
        return self._view

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._view = None
        self._owner._checkin()
        return False


# ----------------------------------------------------------------------
# 泄漏扫描
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class LeakFinding:
    """一条泄漏记录（只记录元信息，不回显明文）。"""

    type_name: str   # 持有副本的对象类型
    obj_len: int     # 对象长度
    offset: int      # 明文模式在对象中的偏移


@dataclass
class LeakReport:
    """扫描结果。"""

    pattern_length: int
    findings: list = field(default_factory=list)
    scanned_objects: int = 0
    truncated: bool = False

    @property
    def copy_count(self) -> int:
        """检测到的意外副本数量。"""
        return len(self.findings)

    @property
    def leaked(self) -> bool:
        return bool(self.findings)

    def summary(self) -> str:
        if not self.leaked:
            return f"未检测到意外复制（扫描 {self.scanned_objects} 个对象）"
        kinds = {}
        for f in self.findings:
            kinds[f.type_name] = kinds.get(f.type_name, 0) + 1
        detail = ", ".join(f"{k}x{v}" for k, v in sorted(kinds.items()))
        return (
            f"检测到 {self.copy_count} 份意外副本（{detail}），"
            f"扫描 {self.scanned_objects} 个对象"
        )


class LeakScanner:
    """在解释器内存中搜索敏感字节模式的残留副本。

    扫描范围：``gc.get_objects()`` 覆盖的全部 GC 追踪对象，以及从它们
    出发、按 ``gc.get_referents`` 广度优先展开的非追踪对象（bytes /
    bytearray / str 等叶子类型本身不被 GC 追踪，必须靠展开才能看到）。
    """

    #: 会被展开引用的容器类型
    _CONTAINERS = (dict, list, tuple, set, frozenset)

    def __init__(
        self,
        pattern: bytes,
        *,
        extra_exclude_ids: Set[int] = frozenset(),
        scan_strings: bool = True,
        max_depth: int = 3,
        max_objects: int = 200_000,
    ):
        if not pattern:
            raise ValueError("pattern 不能为空")
        self._pattern = bytes(pattern)
        self._exclude_ids = {id(self._pattern), id(pattern)} | set(extra_exclude_ids)
        self._scan_strings = scan_strings
        self._max_depth = max_depth
        self._max_objects = max_objects

    def scan(self) -> LeakReport:
        gc.collect()  # 先回收垃圾，避免把待回收的临时对象误报为泄漏
        pattern = self._pattern
        pattern_str = pattern.decode("latin1") if self._scan_strings else None
        if pattern_str is not None:
            self._exclude_ids.add(id(pattern_str))  # 防止扫描器自匹配
        report = LeakReport(pattern_length=len(pattern))
        seen = set()  # 已检查对象的 id，防止重复计数

        def check(obj) -> None:
            oid = id(obj)
            if oid in seen or oid in self._exclude_ids:
                return
            seen.add(oid)
            try:
                if isinstance(obj, (bytes, bytearray)):
                    off = obj.find(pattern)
                elif isinstance(obj, memoryview):
                    data = obj.tobytes()
                    self._exclude_ids.add(id(data))
                    off = data.find(pattern)
                elif isinstance(obj, str) and pattern_str is not None:
                    off = obj.find(pattern_str)
                else:
                    return
            except (ValueError, TypeError):
                return
            if off >= 0:
                report.findings.append(
                    LeakFinding(type_name=type(obj).__name__, obj_len=len(obj), offset=off)
                )

        # 迭代式 BFS。种子有两类：
        # 1. gc.get_objects()：所有 GC 追踪对象（容器、模块、列表等）；
        # 2. 各线程的完整调用栈（沿 f_back 走）：运行中的栈帧在 3.12 上不会被
        #    gc.get_objects() 枚举，gc.get_referents(frame) 也为空，必须显式
        #    物化 f_locals 才能看到仅被函数局部变量持有的副本（这正是
        #    "光把变量置空是不够的"要检测的情形）。
        # 扫描前先访问每个栈帧的 f_locals，触发"快局部变量 → locals 字典"
        # 的同步：否则此前被物化为字典的 f_locals（字典被 GC 追踪，会出现在
        # gc.get_objects() 中）可能仍持有已被 del 的局部变量的旧引用，
        # 造成误报。访问 f_locals 会就地刷新同一个字典对象。
        seed_frames = []
        for frame in sys._current_frames().values():
            while frame is not None:
                seed_frames.append(frame)
                frame = frame.f_back
        for frame in seed_frames:
            try:
                frame.f_locals  # noqa: B018  触发同步
            except Exception:
                pass
        frontier = list(gc.get_objects())
        frontier.extend(seed_frames)
        depth = 0
        scanned = 0
        while frontier and depth <= self._max_depth and scanned < self._max_objects:
            next_frontier = []
            for obj in frontier:
                scanned += 1
                if scanned > self._max_objects:
                    report.truncated = True
                    break
                check(obj)
                if depth < self._max_depth and (
                    gc.is_tracked(obj)
                    or isinstance(obj, self._CONTAINERS)
                    or isinstance(obj, types.FrameType)  # 帧不被 GC 追踪，必须特判
                ):
                    if isinstance(obj, types.FrameType):
                        try:
                            next_frontier.append(obj.f_locals)
                        except Exception:
                            pass
                    try:
                        for ref in gc.get_referents(obj):
                            if id(ref) not in seen and (
                                isinstance(
                                    ref, (bytes, bytearray, memoryview, str) + self._CONTAINERS
                                )
                                or gc.is_tracked(ref)
                            ):
                                next_frontier.append(ref)
                    except Exception:
                        pass
            frontier = next_frontier
            depth += 1
        report.scanned_objects = scanned
        return report
