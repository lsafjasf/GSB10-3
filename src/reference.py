"""Naive reference implementation used for differential testing.

Groups records by five-tuple, sorts each group by (start_ts, end_ts, seq),
then walks the group once applying the gap rule. Deliberately simple and
independent from FlowAggregator's incremental chain logic.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from flow_aggregator import FlowRecord, FlowSession


def reference_aggregate(records: List[FlowRecord],
                        idle_timeout: float) -> Dict[tuple, List[FlowSession]]:
    groups: Dict[tuple, List[FlowRecord]] = defaultdict(list)
    for rec in records:
        groups[rec.key].append(rec)

    out: Dict[tuple, List[FlowSession]] = {}
    for key, recs in groups.items():
        recs.sort(key=lambda r: (r.start_ts, r.end_ts, r.seq))
        sessions: List[FlowSession] = []
        cur = FlowSession(key=key, session_id=0, start_ts=recs[0].start_ts,
                          end_ts=recs[0].end_ts, byte_count=recs[0].byte_count,
                          packet_count=recs[0].packet_count, record_count=1,
                          last_seen=recs[0].end_ts)
        for rec in recs[1:]:
            if rec.start_ts - cur.end_ts <= idle_timeout:
                cur.start_ts = min(cur.start_ts, rec.start_ts)
                cur.end_ts = max(cur.end_ts, rec.end_ts)
                cur.last_seen = max(cur.last_seen, rec.end_ts)
                cur.byte_count += rec.byte_count
                cur.packet_count += rec.packet_count
                cur.record_count += 1
            else:
                sessions.append(cur)
                cur = FlowSession(key=key, session_id=0,
                                  start_ts=rec.start_ts, end_ts=rec.end_ts,
                                  byte_count=rec.byte_count,
                                  packet_count=rec.packet_count,
                                  record_count=1, last_seen=rec.end_ts)
        sessions.append(cur)
        out[key] = sessions
    return out


def normalize(sessions_by_key: Dict[tuple, List[FlowSession]]) -> List[tuple]:
    """Comparable canonical form (session ids are implementation detail)."""
    rows = []
    for key, sessions in sessions_by_key.items():
        for s in sessions:
            rows.append((key, s.start_ts, s.end_ts, s.byte_count,
                         s.packet_count, s.record_count))
    return sorted(rows)
