"""确定性随机源。

不依赖 random 模块，直接用 hashlib.sha256 做计数器式派生，
保证同一份代码在任何进程、任何机器上输出逐字节一致。
"""

from __future__ import annotations

import hashlib

MASK64 = (1 << 64) - 1


def normalize_seed(seed) -> bytes:
    """把 int / str / bytes 种子规范化为字节串。"""
    if isinstance(seed, bool):
        raise TypeError("seed 不能是 bool")
    if isinstance(seed, int):
        return b"detgen-seed-int:" + str(seed).encode("ascii")
    if isinstance(seed, str):
        return b"detgen-seed-str:" + seed.encode("utf-8")
    if isinstance(seed, (bytes, bytearray)):
        return b"detgen-seed-bytes:" + bytes(seed)
    raise TypeError(f"不支持的种子类型: {type(seed)!r}")


def _splitmix64(state: int):
    """SplitMix64 一步推进，返回 (新状态, 64 位输出)。"""
    state = (state + 0x9E3779B97F4A7C15) & MASK64
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return state, z ^ (z >> 31)


class Source:
    """一个独立的确定性 64 位随机流。"""

    __slots__ = ("_state",)

    def __init__(self, state: int):
        self._state = state & MASK64

    @classmethod
    def from_parts(cls, *parts) -> "Source":
        """由任意上下文片段派生一个流，例如 ("user", "name", 0)。"""
        h = hashlib.sha256()
        for part in parts:
            if isinstance(part, int):
                h.update(part.to_bytes(8, "big", signed=True))
            elif isinstance(part, str):
                h.update(part.encode("utf-8"))
            elif isinstance(part, (bytes, bytearray)):
                h.update(bytes(part))
            else:
                raise TypeError(f"不支持的派生片段: {type(part)!r}")
            h.update(b"\x00")
        return cls(int.from_bytes(h.digest()[:8], "big"))

    def u64(self) -> int:
        self._state, out = _splitmix64(self._state)
        return out

    def rand_index(self, n: int) -> int:
        """均匀返回 [0, n) 中的整数，使用拒绝采样避免取模偏差。"""
        if n <= 0:
            raise ValueError("rand_index 需要 n >= 1")
        if n == 1:
            return 0
        bits = (n - 1).bit_length()
        while True:
            value = self.u64() & ((1 << bits) - 1)
            if value < n:
                return value


def row_source(seed_bytes: bytes, table: str, row_index: int) -> Source:
    """派生某一行的随机源：只依赖 (种子, 表名, 行号)，与总行数无关。

    这是“规模变化时已有行保持稳定”的关键。
    """
    digest = hashlib.sha256(
        b"detgen-row-v1\x00"
        + seed_bytes
        + b"\x00"
        + table.encode("utf-8")
        + b"\x00"
        + row_index.to_bytes(8, "big")
    ).digest()
    return Source(int.from_bytes(digest[:8], "big"))
