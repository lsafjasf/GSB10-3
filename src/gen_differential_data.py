"""Generate deterministic differential-test fixtures.

Writes data/differential_data.csv with one row per case:
  case, arrival_order, idle_timeout, record fields..., expected sessions...

The expected sessions are produced by the naive reference and cross-checked
against FlowAggregator, so the CSV is an independently inspectable oracle.

Run: python3 gen_differential_data.py
"""

import csv
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flow_aggregator import FlowAggregator, FlowRecord  # noqa: E402
from reference import reference_aggregate  # noqa: E402


def run_case(name, idle_timeout, records, shuffle_seed=None):
    arrival = list(records)
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(arrival)
    for i, r in enumerate(arrival):
        object.__setattr__(r, "seq", i)  # arrival order tie-break

    agg = FlowAggregator(idle_timeout=idle_timeout,
                         max_active_sessions=10**9)
    for r in arrival:
        assert agg.add(r) == []
    got = sorted((s.key, s.start_ts, s.end_ts, s.byte_count, s.packet_count,
                  s.record_count) for s in agg.flush())

    ref = reference_aggregate(arrival, idle_timeout)
    want = sorted((k, s.start_ts, s.end_ts, s.byte_count, s.packet_count,
                   s.record_count) for k, ss in ref.items() for s in ss)
    assert got == want, f"case {name}: aggregator != reference"
    return arrival, got


def R(start, end, b, p, port=5000, proto="tcp"):
    return FlowRecord(proto, "10.0.0.1", port, "10.0.0.2", 80,
                      start, end, b, p)


def main():
    cases = []

    # 1. single record
    cases.append(("single_record", 10.0, [R(0, 5, 300, 3)], None))
    # 2. exact boundary: gap == idle_timeout merges
    cases.append(("boundary_gap_eq_timeout", 10.0,
                  [R(0, 5, 100, 1), R(15, 20, 200, 2)], None))
    # 3. just beyond boundary: gap > idle_timeout splits
    cases.append(("boundary_gap_gt_timeout", 10.0,
                  [R(0, 5, 100, 1), R(15.5, 20, 200, 2)], None))
    # 4. five-tuple reuse after timeout -> two sessions
    cases.append(("tuple_reuse_after_timeout", 10.0,
                  [R(0, 5, 100, 1), R(100, 110, 200, 2)], None))
    # 5. out-of-order: late record merges into earlier session
    cases.append(("ooo_late_merge", 10.0,
                  [R(0, 5, 100, 1), R(30, 35, 300, 3), R(8, 12, 200, 2)],
                  None))
    # 6. out-of-order: late long record bridges two sessions
    cases.append(("ooo_bridge_fuse", 10.0,
                  [R(0, 5, 100, 1), R(20, 25, 300, 3), R(4, 21, 200, 2)],
                  None))
    # 7. fully reversed arrival of one logical session
    cases.append(("ooo_reversed", 10.0,
                  [R(20, 25, 300, 3), R(10, 15, 200, 2), R(0, 5, 100, 1)],
                  None))
    # 8. zero-length records at identical timestamps
    cases.append(("zero_duration_same_ts", 10.0,
                  [R(5, 5, 100, 1), R(5, 5, 200, 2), R(5, 5, 300, 3)], None))
    # 9. zero idle timeout: any positive gap splits
    cases.append(("zero_timeout", 0.0,
                  [R(0, 1, 100, 1), R(1, 2, 200, 2), R(2.5, 3, 300, 3)],
                  None))
    # 10. randomized shuffled workload, deterministic seed
    rng = random.Random(20261003)
    recs = []
    for _ in range(60):
        start = rng.uniform(0, 200)
        recs.append(R(start, start + rng.uniform(0, 3),
                      rng.randrange(1, 5000), rng.randrange(1, 50),
                      port=rng.choice([5000, 5001, 5002])))
    cases.append(("random_shuffled_seed20261003", 15.0, recs, 7))

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.normpath(os.path.join(out_dir, "differential_data.csv"))
    fields = ["case", "arrival_idx", "idle_timeout", "proto", "src_ip",
              "src_port", "dst_ip", "dst_port", "start_ts", "end_ts",
              "bytes", "packets", "n_expected_sessions",
              "expected_sessions(key,start,end,bytes,packets,n_records)"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for name, timeout, records, seed in cases:
            arrival, sessions = run_case(name, timeout, records, seed)
            expected = ";".join(
                f"{k[0]}|{k[1]}|{k[2]}|{k[3]}|{k[4]}:{st}:{en}:{b}:{p}:{n}"
                for k, st, en, b, p, n in sessions)
            for idx, r in enumerate(arrival):
                w.writerow([name, idx, timeout, r.proto, r.src_ip,
                            r.src_port, r.dst_ip, r.dst_port, r.start_ts,
                            r.end_ts, r.byte_count, r.packet_count,
                            len(sessions) if idx == 0 else "",
                            expected if idx == 0 else ""])
    print(f"wrote {path} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
