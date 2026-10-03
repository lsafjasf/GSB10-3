"""参考实现（对拍基准）：离线、按时间排序后直接分段。

算法: 对每个五元组，把记录按 (start, end) 排序，顺序扫描；
若当前记录与上一会话的 gap > idle_timeout 则切新会话，否则合并。
语义与 FlowAggregator 的切分判据完全一致，但实现独立、无容量/老化逻辑，
用于交叉验证（对拍）聚合结果。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List

from .aggregator import FlowRecord, FlowSession, FiveTuple, interval_gap


def aggregate_reference(records: Iterable[FlowRecord], idle_timeout: float) -> List[FlowSession]:
    by_key: Dict[FiveTuple, List[FlowRecord]] = defaultdict(list)
    for rec in records:
        by_key[rec.key].append(rec)

    sessions: List[FlowSession] = []
    for key in sorted(by_key):
        recs = sorted(by_key[key], key=lambda r: (r.start, r.end))
        current = FlowSession.from_record(recs[0])
        for rec in recs[1:]:
            gap = interval_gap(current.start, current.end, rec.start, rec.end)
            if gap > idle_timeout:
                sessions.append(current)
                current = FlowSession.from_record(rec)
            else:
                current.absorb(rec)
        sessions.append(current)
    return sessions


def sessions_to_canonical(sessions: Iterable[FlowSession]) -> List[dict]:
    """规范化输出：按 (key, start) 排序的 dict 列表，便于直接比较。"""
    return sorted(
        (s.to_dict() for s in sessions),
        key=lambda d: (d["src_ip"], d["dst_ip"], d["src_port"],
                       d["dst_port"], d["protocol"], d["start"], d["end"]),
    )
