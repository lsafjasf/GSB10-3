"""分片重组库（仅依赖 Python 标准库）。

特性：
- 按 (frag_id, offset) 重组，末片以 more=False 标记，重组完成后立即归还并释放缓冲。
- 分片重叠时“先到达的数据为准”：后到分片与已存区间重叠的部分被裁掉。
- 每个未完成重组的分片组有绝对超时（自首片到达起算），超时即丢弃。
- 全局缓冲有总字节上界；到达上界时按“最久未活动组优先”(LRU) 清退。
- 时间可注入：clock 为无参可调用，返回单调时间（默认 time.monotonic）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


@dataclass
class EvictionRecord:
    """一条清退记录。reason: 'timeout'（超时）| 'capacity'（总量上界）| 'reject'（单分片超限）。"""

    ts: float
    frag_id: object
    reason: str
    bytes_freed: int
    detail: str = ""

    def __str__(self) -> str:
        return (
            f"[{self.ts:.3f}] evict id={self.frag_id!r} reason={self.reason} "
            f"freed={self.bytes_freed}B {self.detail}".rstrip()
        )


@dataclass
class _Segment:
    start: int
    data: bytes

    @property
    def end(self) -> int:
        return self.start + len(self.data)


@dataclass
class _Group:
    frag_id: object
    first_seen: float
    last_seen: float
    segments: List[_Segment] = field(default_factory=list)
    total_bytes: int = 0
    final_end: Optional[int] = None  # 末片（more=False）声明的结束偏移；None 表示尚未收到末片

    def insert(self, offset: int, data: bytes) -> int:
        """插入一段数据，丢弃与已有区间重叠的部分（先到为准）。返回实际新增字节数。"""
        added_total = 0
        # 用已有区间裁掉新数据 [offset, offset+len) 的重叠部分
        pieces: List[Tuple[int, bytes]] = [(offset, data)]
        for seg in self.segments:
            next_pieces: List[Tuple[int, bytes]] = []
            for p_off, p_data in pieces:
                p_end = p_off + len(p_data)
                if seg.end <= p_off or seg.start >= p_end:
                    next_pieces.append((p_off, p_data))  # 不相交
                    continue
                # 与 [seg.start, seg.end) 相交，保留相交点两侧的残片
                if p_off < seg.start:
                    cut = seg.start - p_off
                    next_pieces.append((p_off, p_data[:cut]))
                if p_end > seg.end:
                    cut = seg.end - p_off
                    next_pieces.append((seg.end, p_data[cut:]))
            pieces = next_pieces
            if not pieces:
                break
        if not pieces:
            return 0
        pieces.sort(key=lambda x: x[0])
        # 合并与插入（新残片之间互不相交，但可能与其它已有段相邻，仅排序即可；
        # 已有段之间本就互不相交）
        self.segments.extend(_Segment(off, chunk) for off, chunk in pieces)
        self.segments.sort(key=lambda s: s.start)
        added_total = sum(len(chunk) for _, chunk in pieces)
        self.total_bytes += added_total
        return added_total

    def assembled(self) -> Optional[bytes]:
        """区间恰好覆盖 [0, final_end) 且已收到末片时返回完整报文，否则 None。"""
        if self.final_end is None or not self.segments:
            return None
        if self.segments[0].start != 0:
            return None
        cursor = 0
        out = bytearray()
        for seg in self.segments:
            if seg.start != cursor:
                return None
            out += seg.data
            cursor = seg.end
        if cursor != self.final_end:
            return None
        return bytes(out)

    def holes(self) -> List[Tuple[int, int]]:
        """返回空洞区间列表（用于排障/测试）。"""
        holes: List[Tuple[int, int]] = []
        cursor = 0
        for seg in sorted(self.segments, key=lambda s: s.start):
            if seg.start > cursor:
                holes.append((cursor, seg.start))
            cursor = max(cursor, seg.end)
        if self.final_end is not None and cursor < self.final_end:
            holes.append((cursor, self.final_end))
        return holes


@dataclass
class AddResult:
    reassembled: Optional[bytes]   # 本次插入后若重组完成则为完整报文，否则 None
    accepted: bool                 # 分片是否被接受（False=因上界被拒）
    added_bytes: int               # 实际写入缓冲的字节数（重叠部分不计）


class Reassembler:
    def __init__(
        self,
        max_total_bytes: int = 1 << 20,
        group_timeout: float = 30.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if max_total_bytes <= 0:
            raise ValueError("max_total_bytes must be positive")
        if group_timeout <= 0:
            raise ValueError("group_timeout must be positive")
        self._max_total_bytes = max_total_bytes
        self._timeout = group_timeout
        self._clock = clock or time.monotonic
        self._groups: dict = {}
        self.eviction_log: List[EvictionRecord] = []
        self.dropped_inbound: int = 0

    @property
    def buffered_bytes(self) -> int:
        return sum(g.total_bytes for g in self._groups.values())

    @property
    def active_groups(self) -> int:
        return len(self._groups)

    def add_fragment(
        self, frag_id: object, offset: int, data: bytes, more: bool = True
    ) -> AddResult:
        now = self._clock()
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")

        # 1) 先做超时清理（判据：now - first_seen >= group_timeout）
        self.expire(now)

        group = self._groups.get(frag_id)
        if group is None:
            group = _Group(frag_id=frag_id, first_seen=now, last_seen=now)
            self._groups[frag_id] = group

        incoming_len = len(data)
        # 2) 总量上界：先尝试接收；空间不足时按 LRU（last_seen 最早）清退其它组
        if incoming_len > self._max_total_bytes:
            # 单分片超过总上界：无法容纳，拒绝（若该组刚创建则移除空组）
            if group.total_bytes == 0:
                del self._groups[frag_id]
            rec = EvictionRecord(now, frag_id, "reject", 0,
                                 f"fragment {incoming_len}B exceeds cap {self._max_total_bytes}B")
            self.eviction_log.append(rec)
            self.dropped_inbound += 1
            return AddResult(None, False, 0)

        projected = self.buffered_bytes
        if projected + incoming_len > self._max_total_bytes:
            # 按 last_seen 升序清退，跳过正在写入的组
            for other in sorted(self._groups.values(), key=lambda g: g.last_seen):
                if other.frag_id == frag_id:
                    continue
                if projected + incoming_len <= self._max_total_bytes:
                    break
                self._drop_group(other.frag_id, "capacity", now,
                                 detail=f"make room for {incoming_len}B")
                projected -= other.total_bytes
            # 清退其它组后仍放不下（本组自身占用过多）：拒绝该分片
            if projected + incoming_len > self._max_total_bytes:
                if group.total_bytes == 0 and frag_id in self._groups:
                    del self._groups[frag_id]
                rec = EvictionRecord(now, frag_id, "reject", 0,
                                     f"inspectable cap {self._max_total_bytes}B; "
                                     f"need {projected + incoming_len}B")
                self.eviction_log.append(rec)
                self.dropped_inbound += 1
                return AddResult(None, False, 0)

        # 3) 写入（重叠部分在 insert 内被裁剪，先到数据为准）
        added = group.insert(offset, bytes(data))
        group.last_seen = now
        if not more:
            end = offset + incoming_len  # 末片声明：以片尾为准（重叠裁剪不影响声明）
            if group.final_end is None or end > group.final_end:
                group.final_end = end

        # 4) 检查是否完成
        result = group.assembled()
        if result is not None:
            del self._groups[frag_id]
            return AddResult(result, True, added)
        return AddResult(None, True, added)

    def expire(self, now: Optional[float] = None) -> List[EvictionRecord]:
        """超时判据：now - first_seen >= group_timeout 的未完成组被丢弃。"""
        if now is None:
            now = self._clock()
        expired = [
            gid for gid, g in self._groups.items()
            if now - g.first_seen >= self._timeout
        ]
        records = [self._drop_group(gid, "timeout", now) for gid in expired]
        return records

    def _drop_group(self, frag_id: object, reason: str, now: float,
                    detail: str = "") -> EvictionRecord:
        group = self._groups.pop(frag_id)
        rec = EvictionRecord(
            ts=now, frag_id=frag_id, reason=reason,
            bytes_freed=group.total_bytes,
            detail=(detail + f" holes={group.holes()}").strip(),
        )
        self.eviction_log.append(rec)
        return rec

    def holes(self, frag_id: object) -> List[Tuple[int, int]]:
        group = self._groups.get(frag_id)
        return group.holes() if group is not None else []
