"""IP 风格的分片重组器（仅标准库）。

核心规则
--------
* 每个分片组以 ``identification`` 标识，由若干 (offset, data) 分片组成。
* ``final=True`` 的分片标记组尾：组总长 = offset + len(data)。
* 当覆盖区间 [0, total_len) 全部连续到达时，组重组完成。
* 分片区间互相重叠时，**先到达的字节生效**，后到分片的重叠部分被裁剪丢弃，
  被裁剪字节数计入重叠统计。
* 每个组在首个分片到达后经过 ``timeout`` 秒仍未收齐即过期（收到新分片不续命）。
* 所有在途组占用的字节总数不得超过 ``max_total_bytes``；放不下时按
  先过期清扫、后 LRU（last_seen 最久未更新，再按建组先后）整组清退。
* 时钟通过构造参数注入，方便测试（默认 ``time.monotonic``）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Fragment:
    """一个数据分片。

    :param identification: 分片组标识。
    :param offset: 数据在原始报文中的字节偏移。
    :param data: 分片载荷。
    :param final: 是否为组尾分片（携带组总长度）。
    """

    identification: object
    offset: int
    data: bytes
    final: bool = False


@dataclass
class Delivery:
    """重组完成事件。"""

    identification: object
    payload: bytes
    overlap_bytes: int  # 整个组生命周期内被先到数据裁掉的字节数


@dataclass
class EvictionRecord:
    """清退记录（超时或容量原因）。"""

    identification: object
    reason: str  # "timeout" 或 "capacity"
    at_time: float
    age: float
    bytes_freed: int
    covered_bytes: int
    total_length: Optional[int]
    fragments_kept: int
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "identification": self.identification,
            "reason": self.reason,
            "at_time": self.at_time,
            "age": round(self.age, 6),
            "bytes_freed": self.bytes_freed,
            "covered_bytes": self.covered_bytes,
            "total_length": self.total_length,
            "fragments_kept": self.fragments_kept,
            "detail": self.detail,
        }


class ReassemblyError(Exception):
    """重组相关错误的基类。"""


class ProtocolError(ReassemblyError):
    """分片本身非法（负偏移 / 尾长冲突等）。"""


class FragmentTooLargeError(ReassemblyError):
    """单个分片超过总容量，任何情况下都无法接收。"""


@dataclass
class _Group:
    identification: object
    created_at: float

    # 互不重叠、按起点排序的已覆盖闭开区间 [start, end)
    segments: List[List[int]] = field(default_factory=list)
    data: Dict[Tuple[int, int], bytes] = field(default_factory=dict)
    end: Optional[int] = None
    fragment_count: int = 0
    overlap_bytes: int = 0
    last_seen: float = 0.0

    def __post_init__(self) -> None:
        self.last_seen = self.created_at

    @property
    def covered_bytes(self) -> int:
        return sum(end - start for start, end in self.segments)

    def _uncovered(self, start: int, end: int) -> List[Tuple[int, int]]:
        """计算新区间相对已覆盖区间的未覆盖部分。"""
        pieces: List[Tuple[int, int]] = []
        cursor = start
        for seg_start, seg_end in self.segments:
            if seg_end <= cursor:
                continue
            if seg_start >= end:
                break
            if seg_start > cursor:
                pieces.append((cursor, min(seg_start, end)))
            cursor = max(cursor, seg_end)
            if cursor >= end:
                break
        if cursor < end:
            pieces.append((cursor, end))
        return pieces

    def preview(
        self, offset: int, length: int, final: bool
    ) -> Tuple[List[Tuple[int, bytes]], int, bool]:
        """返回 (将真正写入的子段列表, 被先到数据裁掉的字节数, 是否净增加)。

        不修改任何状态。
        """
        end = offset + length
        new_end = end if final else None
        # 两者同时存在时已在 add() 前置校验一致，这里任取其一即可。
        effective_end = self.end if self.end is not None else new_end
        if effective_end is not None:
            end = min(end, effective_end)
            if offset >= end:
                return [], length, False
        pieces = self._uncovered(offset, end)
        written = sum(e - s for s, e in pieces)
        return pieces, length - written, bool(pieces)

    def insert(self, offset: int, payload: bytes, final: bool) -> int:
        """插入分片，返回净增加的字节数；重叠字节累计到 overlap_bytes。"""
        end = offset + len(payload)
        if final:
            if self.end is not None and self.end != end:
                raise ProtocolError(
                    f"组 {self.identification!r}: 尾分片声明长度 {end} 与已声明 {self.end} 冲突"
                )
            self.end = end
        if self.end is not None:
            end = min(end, self.end)
            if offset >= end:
                self.overlap_bytes += len(payload)
                return 0
        pieces = self._uncovered(offset, end)
        written = sum(e - s for s, e in pieces)
        self.overlap_bytes += len(payload) - written
        added = 0
        for piece_start, piece_end in pieces:
            chunk = payload[piece_start - offset : piece_end - offset]
            self.data[(piece_start, piece_end)] = chunk
            self.segments.append([piece_start, piece_end])
            added += piece_end - piece_start
        self.segments.sort()
        self._merge()
        self.fragment_count += 1
        return added

    def _merge(self) -> None:
        """合并相邻区间，data 里的字节也随之拼接。"""
        merged: List[List[int]] = []
        for seg in self.segments:
            if merged and seg[0] <= merged[-1][1]:
                prev_start, prev_end = merged[-1]
                left = self.data.pop((prev_start, prev_end))
                right = self.data.pop((seg[0], seg[1]))
                if seg[0] < prev_end:  # 理论上不会出现（插入前已裁剪），防御性处理
                    right = right[prev_end - seg[0] :]
                merged[-1][1] = seg[1]
                self.data[(prev_start, seg[1])] = left + right
            else:
                merged.append(seg)
        self.segments = merged

    def complete(self) -> bool:
        # 区间互不重叠且有序：覆盖数等于总长且首段从 0 开始 ⇔ [0, end) 连续覆盖。
        # 该写法同时兼容空报文（end == 0，无任何区间）。
        return (
            self.end is not None
            and self.covered_bytes == self.end
            and (not self.segments or self.segments[0][0] == 0)
        )

    def assemble(self) -> bytes:
        if not self.complete():
            raise ReassemblyError("组尚未收齐，无法重组")
        return self.data.get((0, self.end), b"")

    def expired(self, now: float, timeout: float) -> bool:
        # 判据：建组时间（首个分片到达时刻）+ timeout，新分片不重置。
        return now - self.created_at >= timeout


class Reassembler:
    """分片重组器。

    :param timeout: 组超时秒数（从首个分片到达计时，不随后续分片延长）。
    :param max_total_bytes: 全部分片组已覆盖字节数的总量上界。
    :param time_func: 可注入的时钟，返回单调递增的秒数浮点数。
    :param on_evict: 可选回调，每产生一条清退记录时调用。
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_total_bytes: int = 1 << 20,
        time_func: Optional[Callable[[], float]] = None,
        on_evict: Optional[Callable[[EvictionRecord], None]] = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须为正数")
        if max_total_bytes <= 0:
            raise ValueError("max_total_bytes 必须为正数")
        self._timeout = timeout
        self._capacity = max_total_bytes
        self._clock = time_func or time.monotonic
        self._on_evict = on_evict
        self._groups: Dict[object, _Group] = {}
        self._used = 0
        self._birth_seq = 0
        self.eviction_log: List[EvictionRecord] = []

    @property
    def used_bytes(self) -> int:
        return self._used

    @property
    def buffered_groups(self) -> int:
        return len(self._groups)

    def add(self, fragment: Fragment) -> Optional[Delivery]:
        """投递一个分片；组收齐时返回 :class:`Delivery`，否则返回 None。"""
        if fragment.offset < 0:
            raise ProtocolError("offset 不允许为负数")
        if not isinstance(fragment.data, (bytes, bytearray, memoryview)):
            raise ProtocolError("data 必须是 bytes-like")
        data = bytes(fragment.data)
        now = self._clock()

        if len(data) > self._capacity:
            raise FragmentTooLargeError(
                f"分片 {len(data)} 字节超过总容量 {self._capacity}，无法接收"
            )

        # 尾分片与既有组的长度冲突属于协议错误：先校验，再做任何状态变更。
        existing = self._groups.get(fragment.identification)
        if existing is not None and fragment.final and existing.end is not None:
            end = fragment.offset + len(data)
            if end != existing.end:
                raise ProtocolError(
                    f"组 {fragment.identification!r}: 尾分片声明长度 {end} 与已声明 {existing.end} 冲突"
                )

        # 1) 先清扫已超时组（无论是否需要空间都做，保证超时及时释放）。
        self.sweep(now)

        # 2) 容量准入：计算本分片的净新增，不够就按 LRU 整组清退。
        group = self._groups.get(fragment.identification)
        pieces, _, _ = (
            group.preview(fragment.offset, len(data), fragment.final)
            if group is not None
            else ([(fragment.offset, fragment.offset + len(data))], 0, True)
        )
        need = sum(e - s for s, e in pieces)
        if self._used + need > self._capacity:
            self._evict_for_room(now, need, protect=fragment.identification)
            group = self._groups.get(fragment.identification)
            if self._used + need > self._capacity:
                # 其余组全退掉仍放不下：放弃该组自身。
                if group is not None:
                    self._drop(group.identification, now, "capacity",
                              "仅余本组仍超过总量上界，整组清退")
                raise FragmentTooLargeError(
                    f"接收分片需要 {need} 字节，超过可用/总容量 {self._capacity}"
                )

        # 3) 建组 / 写入。
        if group is None:
            group = _Group(fragment.identification, now)
            self._groups[group.identification] = group
        self._used += group.insert(fragment.offset, data, fragment.final)
        group.last_seen = now

        # 4) 收齐则立即交付并释放。
        if group.complete():
            payload = group.assemble()
            delivery = Delivery(group.identification, payload, group.overlap_bytes)
            self._remove_group(group)
            return delivery
        return None

    def sweep(self, now: Optional[float] = None) -> List[EvictionRecord]:
        """丢弃所有已超时组，返回本次产生的清退记录。"""
        if now is None:
            now = self._clock()
        records = []
        for gid in [g.identification for g in self._groups.values()]:
            group = self._groups[gid]
            if group.expired(now, self._timeout):
                records.append(
                    self._drop(
                        gid,
                        now,
                        "timeout",
                        f"建组后 {now - group.created_at:.3f}s 仍未收齐"
                        + (f"（总长 {group.end}，仅覆盖 {group.covered_bytes}）"
                           if group.end is not None else "（未收到尾分片，总长未知）"),
                    )
                )
        return records

    def _evict_for_room(self, now: float, need: int, protect: object) -> None:
        """按 LRU（last_seen, 建组序号）整组清退，直到腾出 need 字节。"""
        while self._used + need > self._capacity:
            candidates = [g for g in self._groups.values() if g.identification != protect]
            if not candidates:
                return
            victim = min(candidates, key=lambda g: (g.last_seen, g.created_at))
            self._drop(
                victim.identification,
                now,
                "capacity",
                f"为新分片腾出 {need} 字节，LRU 选中（last_seen={victim.last_seen}）",
            )

    def _drop(self, gid: object, now: float, reason: str, detail: str) -> EvictionRecord:
        group = self._groups[gid]
        record = EvictionRecord(
            identification=gid,
            reason=reason,
            at_time=now,
            age=now - group.created_at,
            bytes_freed=group.covered_bytes,
            covered_bytes=group.covered_bytes,
            total_length=group.end,
            fragments_kept=group.fragment_count,
            detail=detail,
        )
        self._remove_group(group)
        self.eviction_log.append(record)
        if self._on_evict is not None:
            self._on_evict(record)
        return record

    def _remove_group(self, group: _Group) -> None:
        self._used -= group.covered_bytes
        del self._groups[group.identification]
        group.data.clear()
        group.segments.clear()
