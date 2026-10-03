"""Differential test: FlowAggregator vs the naive sorted reference.

For random workloads (including heavy out-of-order arrival) we assert:
  1. identical session sets (key, start, end, bytes, packets, record_count)
  2. byte/packet totals equal the plain per-record sums
  3. session start/end equal min/max of the records merged into them

Run: python3 differential_test.py [--trials N] [--seed S]
"""

import argparse
import random
import sys

from flow_aggregator import FlowAggregator, FlowRecord
from reference import normalize, reference_aggregate


def make_records(rng: random.Random, n_records: int, n_keys: int,
                 horizon: float):
    keys = []
    for _ in range(n_keys):
        keys.append((
            rng.choice(["tcp", "udp", "icmp"]),
            f"10.{rng.randrange(3)}.{rng.randrange(3)}.{rng.randrange(3)}",
            rng.randrange(1024, 60000),
            f"172.16.{rng.randrange(3)}.{rng.randrange(3)}",
            rng.choice([80, 443, 53, 8080]),
        ))
    records = []
    for seq in range(n_records):
        proto, sip, sport, dip, dport = rng.choice(keys)
        start = rng.uniform(0, horizon)
        dur = rng.choice([0.0, rng.uniform(0, 2), rng.uniform(0, 20)])
        records.append(FlowRecord(
            proto=proto, src_ip=sip, src_port=sport, dst_ip=dip,
            dst_port=dport, start_ts=start, end_ts=start + dur,
            byte_count=rng.randrange(0, 10**6),
            packet_count=rng.randrange(0, 10**4), seq=seq))
    return records


def run_trial(rng: random.Random, n_records: int, n_keys: int,
              horizon: float, idle_timeout: float, shuffle: bool) -> None:
    records = make_records(rng, n_records, n_keys, horizon)
    arrival = list(records)
    if shuffle:
        rng.shuffle(arrival)

    agg = FlowAggregator(idle_timeout=idle_timeout,
                         max_active_sessions=10**9)
    for rec in arrival:
        assert agg.add(rec) == [], "unexpected eviction with huge cap"
    got = {}
    for s in agg.flush():
        got.setdefault(s.key, []).append(s)
    want = reference_aggregate(records, idle_timeout)

    got_rows, want_rows = normalize(got), normalize(want)
    assert got_rows == want_rows, (
        f"session mismatch\n got: {got_rows}\nwant: {want_rows}")

    # invariant: totals equal plain per-record sums
    assert sum(s.byte_count for ss in got.values() for s in ss) == \
        sum(r.byte_count for r in records)
    assert sum(s.packet_count for ss in got.values() for s in ss) == \
        sum(r.packet_count for r in records)
    assert sum(s.record_count for ss in got.values() for s in ss) == \
        len(records)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=118)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    for trial in range(args.trials):
        n_records = rng.choice([1, 2, 5, 20, 100, 500])
        n_keys = rng.choice([1, 2, 3, 8, 30])
        horizon = rng.choice([10.0, 100.0, 1000.0])
        idle_timeout = rng.choice([0.0, 1.0, 5.0, 30.0])
        shuffle = rng.random() < 0.7  # most trials arrive out of order
        run_trial(rng, n_records, n_keys, horizon, idle_timeout, shuffle)
    print(f"OK: {args.trials} trials, aggregator == reference, "
          f"sum invariants hold (seed={args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
