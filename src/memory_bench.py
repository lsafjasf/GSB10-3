"""Memory-upper-bound benchmark for the flow aggregator.

Measures resident memory and Python-level allocated memory as a function of
the number of ACTIVE five-tuple keys, then demonstrates that a bounded
aggregate keeps flat memory while processing a much larger export volume
(LRU eviction).

Run: python3 memory_bench.py [--bounded-records N]
Writes: data/memory_data.csv
"""

import argparse
import csv
import gc
import os
import sys
import time as _time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flow_aggregator import FlowAggregator, FlowRecord  # noqa: E402

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def rss_bytes() -> int:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * PAGE_SIZE


def make_record(port, start=0.0):
    return FlowRecord("tcp", "10.0.0.1", 1000 + port, "10.0.0.2", 80,
                      start, start + 1.0, 500, 5, seq=port)


def measure_unbounded(sizes):
    """One fresh process measurement per size keeps RSS comparable; within
    this process we take deltas against an empty aggregator baseline."""
    rows = []
    baseline_rss = rss_bytes()
    for n in sizes:
        gc.collect()
        tracemalloc.start()
        rss0 = rss_bytes()
        agg = FlowAggregator(idle_timeout=3600.0)
        for port in range(n):
            assert agg.add(make_record(port)) == []
        gc.collect()
        _, traced_peak = tracemalloc.get_traced_memory()
        rss1 = rss_bytes()
        rows.append(dict(scenario=f"active_keys={n}", active_keys=n,
                         active_sessions=n, total_records=n,
                         evicted=0, rss_kb=max(0, rss1 - rss0) // 1024,
                         traced_kb=traced_peak // 1024))
        tracemalloc.stop()
        del agg
        gc.collect()
    return rows, baseline_rss


def measure_bounded(cap, total_records):
    """Many records, few live keys: memory must stay bounded by cap."""
    gc.collect()
    tracemalloc.start()
    rss0 = rss_bytes()
    agg = FlowAggregator(idle_timeout=3600.0, max_active_sessions=cap)
    max_seen_keys = 0
    finalized = 0
    for i in range(total_records):
        finalized += len(agg.add(make_record(i % (cap * 5), float(i))))
        if i % 100000 == 0:
            max_seen_keys = max(max_seen_keys, agg.active_keys())
            assert agg.active_keys() <= cap
    max_seen_keys = max(max_seen_keys, agg.active_keys())
    gc.collect()
    _, traced_peak = tracemalloc.get_traced_memory()
    rss1 = rss_bytes()
    tracemalloc.stop()
    assert max_seen_keys <= cap
    return dict(scenario=f"bounded cap={cap}", active_keys=agg.active_keys(),
                active_sessions=agg.active_sessions(),
                total_records=total_records, evicted=agg.evicted_keys,
                rss_kb=max(0, rss1 - rss0) // 1024,
                traced_kb=traced_peak // 1024)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bounded-records", type=int, default=1_000_000)
    args = ap.parse_args()

    sizes = [100, 1000, 5000, 10000, 50000, 100000]
    rows, baseline = measure_unbounded(sizes)
    bounded = measure_bounded(cap=10000, total_records=args.bounded_records)
    rows.append(bounded)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.normpath(os.path.join(out_dir, "memory_data.csv"))
    fields = ["scenario", "active_keys", "active_sessions", "total_records",
              "evicted", "rss_kb", "traced_kb"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"process baseline RSS: {baseline // 1024} KB")
    print(f"{'scenario':<22}{'keys':>8}{'sess':>8}{'records':>10}"
          f"{'evicted':>10}{'rss_delta_kb':>14}{'traced_kb':>12}"
          f"{'bytes/key':>11}")
    for r in rows:
        per_key = (r["traced_kb"] * 1024 / r["active_keys"]
                   if r["active_keys"] else 0)
        print(f"{r['scenario']:<22}{r['active_keys']:>8}"
              f"{r['active_sessions']:>8}{r['total_records']:>10}"
              f"{r['evicted']:>10}{r['rss_kb']:>14}{r['traced_kb']:>12}"
              f"{per_key:>11.0f}")
    print(f"\nwrote {out_path}")
    print("Upper bound: live memory ~= base + active_keys * bytes_per_key;")
    print("with max_active_sessions=C, active keys <= C for any input size.")


if __name__ == "__main__":
    main()
