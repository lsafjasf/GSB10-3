"""流记录聚合：按五元组 + 空闲超时把拆分的流记录合并成会话。

核心语义（边界判据）
--------------------
* 五元组: (src_ip, dst_ip, src_port, dst_port, protocol)，精确匹配，不做方向归一化。
* 记录区间: 一条记录占据闭区间 [start, end]（end 缺省等于 start）。
* 空闲间隔: 两条记录（或记录与会话）的间隔定义为
      gap = max(0, max(a.start, b.start) - min(a.end, b.end))
  即区间重叠或相接时 gap 为 0，否则为两者间的时间空隙。
* 切分判据: gap > idle_timeout  -> 必须切成新会话；
            gap <= idle_timeout（含恰好相等）-> 仍属同一会话。
* 老化判据: 在注入时刻 now，now - session.end > idle_timeout -> 会话老化输出；
            now - session.end == idle_timeout 时仍视为活跃（与切分判据一致）。
* 乱序到达: 在同五元组的活跃会话中寻找与该记录可合并（gap <= idle_timeout）
  的会话，取时间距离最近者合并；没有可合并会话则新建会话。
* 输出时机: 会话只在老化 (age_out)、显式冲刷 (flush/drain) 或容量逐出时输出，
  且一律通过返回值（或 on_emit 回调）立即交给调用方，聚合器内部不缓存
  已完成的会话——这是内存硬上界成立的前提。

内存上界
--------
* max_sessions 是活跃会话数的硬上限：活跃会话数恒 <= max_sessions，
  超出时逐出 end 最旧（最久未更新）的会话。
* 内部结构: dict（每会话常数项）+ 索引最小堆（每会话常数项），
  因此总内存 = O(max_sessions)，与已输出/已丢弃的历史无关。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

__all__ = ["FlowRecord", "FlowSession", "FlowAggregator", "interval_gap"]

FiveTuple = Tuple[str, str, int, int, int]


@dataclass(frozen=True)
class FlowRecord:
    """一条设备导出的流记录。时间单位由调用方决定（建议秒，允许浮点）。"""

    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: int
    start: float
    end: float
    packets: int
    bytes: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"record end {self.end} < start {self.start}")
        if self.packets < 0 or self.bytes < 0:
            raise ValueError("packets/bytes must be non-negative")

    @property
    def key(self) -> FiveTuple:
        return (self.src_ip, self.dst_ip, self.src_port, self.dst_port, self.protocol)

    @classmethod
    def from_dict(cls, d: dict) -> "FlowRecord":
        return cls(
            src_ip=d["src_ip"],
            dst_ip=d["dst_ip"],
            src_port=int(d["src_port"]),
            dst_port=int(d["dst_port"]),
            protocol=int(d["protocol"]),
            start=float(d["start"]),
            end=float(d.get("end", d["start"])),
            packets=int(d["packets"]),
            bytes=int(d["bytes"]),
        )


def interval_gap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """两个闭区间之间的时间空隙；重叠或相接时为 0。"""
    return max(0.0, max(a_start, b_start) - min(a_end, b_end))


@dataclass
class FlowSession:
    """聚合中的会话：累计字节/包数，起止时间按合并规则扩展。"""

    key: FiveTuple
    start: float
    end: float
    packets: int
    bytes: int
    record_count: int = 1

    @classmethod
    def from_record(cls, rec: FlowRecord) -> "FlowSession":
        return cls(key=rec.key, start=rec.start, end=rec.end,
                   packets=rec.packets, bytes=rec.bytes)

    def absorb(self, rec: FlowRecord) -> None:
        """合入一条记录：起止时间取包络，字节/包数累加（与逐条求和一致）。"""
        self.start = min(self.start, rec.start)
        self.end = max(self.end, rec.end)
        self.packets += rec.packets
        self.bytes += rec.bytes
        self.record_count += 1

    def gap_to(self, rec: FlowRecord) -> float:
        return interval_gap(self.start, self.end, rec.start, rec.end)

    def to_dict(self) -> dict:
        return {
            "src_ip": self.key[0],
            "dst_ip": self.key[1],
            "src_port": self.key[2],
            "dst_port": self.key[3],
            "protocol": self.key[4],
            "start": self.start,
            "end": self.end,
            "packets": self.packets,
            "bytes": self.bytes,
            "records": self.record_count,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class _IndexedMinHeap:
    """按 (session.end, 插入序号) 排序的索引最小堆。

    会话 end 只会单调增大，因此键更新只需下滤；所有操作 O(log n)。
    堆中每个会话恰好一个条目（无懒删除残留），保证堆本身也有内存上界。
    """

    __slots__ = ("_heap", "_pos", "_counter")

    def __init__(self) -> None:
        self._heap: List[Tuple[float, int, FiveTuple]] = []
        self._pos: Dict[FiveTuple, int] = {}
        self._counter = 0

    def __len__(self) -> int:
        return len(self._heap)

    def __contains__(self, key: FiveTuple) -> bool:
        return key in self._pos

    def _swap(self, i: int, j: int) -> None:
        h = self._heap
        h[i], h[j] = h[j], h[i]
        self._pos[h[i][2]] = i
        self._pos[h[j][2]] = j

    def _sift_up(self, i: int) -> None:
        while i > 0:
            parent = (i - 1) // 2
            if self._heap[i] < self._heap[parent]:
                self._swap(i, parent)
                i = parent
            else:
                break

    def _sift_down(self, i: int) -> None:
        n = len(self._heap)
        while True:
            smallest = i
            left, right = 2 * i + 1, 2 * i + 2
            if left < n and self._heap[left] < self._heap[smallest]:
                smallest = left
            if right < n and self._heap[right] < self._heap[smallest]:
                smallest = right
            if smallest == i:
                return
            self._swap(i, smallest)
            i = smallest

    def push(self, key: FiveTuple, end: float) -> None:
        self._counter += 1
        self._heap.append((end, self._counter, key))
        self._pos[key] = len(self._heap) - 1
        self._sift_up(len(self._heap) - 1)

    def update_end(self, key: FiveTuple, new_end: float) -> None:
        """会话 end 增大后调用（end 单调不减，只需下滤）。"""
        i = self._pos[key]
        end, seq, _ = self._heap[i]
        assert new_end >= end, "session end must be monotonically non-decreasing"
        self._heap[i] = (new_end, seq, key)
        self._sift_down(i)

    def peek(self) -> Tuple[float, FiveTuple]:
        end, _, key = self._heap[0]
        return end, key

    def pop(self) -> Tuple[float, FiveTuple]:
        top = self._heap[0]
        last = self._heap.pop()
        del self._pos[top[2]]
        if self._heap:
            self._heap[0] = last
            self._pos[last[2]] = 0
            self._sift_down(0)
        return top[0], top[2]

    def remove(self, key: FiveTuple) -> None:
        i = self._pos[key]
        last = self._heap.pop()
        del self._pos[key]
        if i < len(self._heap):
            self._heap[i] = last
            self._pos[last[2]] = i
            self._sift_up(i)
            self._sift_down(i)


class FlowAggregator:
    """流记录聚合器。

    参数:
        idle_timeout: 空闲超时（与记录时间同单位）。gap > idle_timeout 切新会话。
        max_sessions: 活跃会话数硬上限（内存上界），超出逐出最旧会话。
        on_emit: 可选回调 fn(session_dict, reason)，reason ∈
                 {"aged", "evicted", "flushed"}。
                 无论是否设置回调，会话字典都会作为返回值交给调用方。
    """

    def __init__(
        self,
        idle_timeout: float,
        max_sessions: int = 1_000_000,
        on_emit: Optional[Callable[[dict, str], None]] = None,
    ) -> None:
        if idle_timeout < 0:
            raise ValueError("idle_timeout must be >= 0")
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        self.idle_timeout = float(idle_timeout)
        self.max_sessions = int(max_sessions)
        self._on_emit = on_emit
        # 每个五元组对应一组活跃会话（乱序/复用时可能多于一个）。
        self._sessions: Dict[FiveTuple, List[FlowSession]] = {}
        self._heap = _IndexedMinHeap()  # 键: (五元组, id(session)) 的复合唯一键
        # 统计
        self.records_in = 0
        self.records_dropped = 0
        self.sessions_created = 0
        self.sessions_aged = 0
        self.sessions_evicted = 0

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _heap_key(self, session: FlowSession) -> Tuple:
        # 堆元素需要全局唯一键；用 (五元组, id(session)) 区分同 key 的多个会话。
        return (session.key, id(session))

    def _emit(self, session: FlowSession, reason: str) -> dict:
        out = session.to_dict()
        if self._on_emit is not None:
            self._on_emit(out, reason)
        return out

    def _remove_session(self, session: FlowSession) -> None:
        bucket = self._sessions[session.key]
        bucket.remove(session)
        if not bucket:
            del self._sessions[session.key]
        self._heap.remove(self._heap_key(session))

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #

    def add(self, rec: FlowRecord, now: Optional[float] = None) -> List[dict]:
        """喂入一条记录，返回本次调用中完成的会话（老化/逐出，通常为空）。

        now 为注入的当前时刻（测试可注入），提供时先执行老化。
        调用方若不复用返回值，已完成的会话即刻释放，不占内存。
        """
        done: List[dict] = []
        if now is not None:
            done.extend(self.age_out(now))
        self.records_in += 1

        bucket = self._sessions.get(rec.key)
        target: Optional[FlowSession] = None
        if bucket:
            best_gap: Optional[float] = None
            for session in bucket:
                gap = session.gap_to(rec)
                if gap <= self.idle_timeout and (best_gap is None or gap < best_gap):
                    target, best_gap = session, gap

        if target is None:
            if len(self._heap) >= self.max_sessions:
                done.append(self._evict_oldest())
            target = FlowSession.from_record(rec)
            self._sessions.setdefault(rec.key, []).append(target)
            self._heap.push(self._heap_key(target), target.end)
            self.sessions_created += 1
        else:
            old_end = target.end
            target.absorb(rec)
            if target.end > old_end:
                self._heap.update_end(self._heap_key(target), target.end)
        return done

    def _evict_oldest(self) -> dict:
        _, heap_key = self._heap.peek()
        key, sid = heap_key
        session = self._find_by_id(key, sid)
        self._remove_session(session)
        self.sessions_evicted += 1
        return self._emit(session, "evicted")

    def _find_by_id(self, key: FiveTuple, sid: int) -> FlowSession:
        for session in self._sessions[key]:
            if id(session) == sid:
                return session
        raise KeyError("session not found (heap/table desync)")

    # ------------------------------------------------------------------ #
    # 老化与冲刷（时间由调用方注入）
    # ------------------------------------------------------------------ #

    def age_out(self, now: float) -> List[dict]:
        """把在时刻 now 已老化（now - end > idle_timeout）的会话输出。

        边界: now - end == idle_timeout 时仍活跃（与切分判据 gap <= timeout 一致）。
        """
        out: List[dict] = []
        while self._heap:
            end, heap_key = self._heap.peek()
            if now - end <= self.idle_timeout:
                break
            key, sid = heap_key
            session = self._find_by_id(key, sid)
            self._remove_session(session)
            self.sessions_aged += 1
            out.append(self._emit(session, "aged"))
        return out

    def flush(self, now: Optional[float] = None) -> List[dict]:
        """先按 now 老化（可选），再把剩余活跃会话全部输出并清空。"""
        out: List[dict] = []
        if now is not None:
            out.extend(self.age_out(now))
        while self._heap:
            _, heap_key = self._heap.peek()
            key, sid = heap_key
            session = self._find_by_id(key, sid)
            self._remove_session(session)
            out.append(self._emit(session, "flushed"))
        return out

    def drain(self) -> List[dict]:
        """等价于不带时间的 flush：输出全部活跃会话。"""
        return self.flush(now=None)

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #

    @property
    def active_count(self) -> int:
        return len(self._heap)

    def active_sessions(self) -> Iterator[FlowSession]:
        for bucket in self._sessions.values():
            yield from bucket

    def stats(self) -> dict:
        return {
            "records_in": self.records_in,
            "records_dropped": self.records_dropped,
            "sessions_created": self.sessions_created,
            "sessions_aged": self.sessions_aged,
            "sessions_evicted": self.sessions_evicted,
            "active_sessions": self.active_count,
        }
