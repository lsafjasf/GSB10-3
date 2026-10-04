"""Estimation-accuracy experiments for the incremental histogram.

Run:  python3 experiments.py

Scenarios: empty table, uniform, concentrated, highly skewed (Zipf),
and heavy churn (mixed inserts/deletes/updates). For each scenario we
compare range-count estimates against exact counts and report error.
A bucket-count sweep shows the capacity/accuracy trade-off, and a demo
prints bucket boundaries as splits and merges happen.
"""

import random
import sys
from bisect import bisect_left
from collections import Counter

sys.path.insert(0, "src")
from incremental_histogram import IncrementalHistogram

DOMAIN = (0.0, 10000.0)


# ----------------------------------------------------------------- truth
class Truth:
    """Exact multiset with fast range-count queries."""

    def __init__(self):
        self.counter = Counter()

    def insert(self, v):
        self.counter[v] += 1

    def delete(self, v):
        self.counter[v] -= 1
        if self.counter[v] == 0:
            del self.counter[v]

    @property
    def total(self):
        return sum(self.counter.values())

    def finalize(self):
        self._keys = sorted(self.counter)
        self._prefix = [0]
        for k in self._keys:
            self._prefix.append(self._prefix[-1] + self.counter[k])

    def range_count(self, a, b):
        lo = bisect_left(self._keys, a)
        hi = bisect_left(self._keys, b)
        return self._prefix[hi] - self._prefix[lo]


# ------------------------------------------------------------- workloads
def gen_uniform(rng, n):
    return [rng.uniform(*DOMAIN) for _ in range(n)]

def gen_concentrated(rng, n):
    out = []
    while len(out) < n:
        v = rng.gauss(5000.0, 5.0)
        if DOMAIN[0] <= v <= DOMAIN[1]:
            out.append(v)
    return out

def gen_zipf(rng, n):
    population = range(10000)
    weights = [1.0 / (i + 1) for i in population]  # Zipf s=1
    return [float(v) for v in rng.choices(population, weights=weights, k=n)]

def build_static(name, values, max_buckets):
    h = IncrementalHistogram(*DOMAIN, max_buckets=max_buckets)
    truth = Truth()
    for v in values:
        h.insert(v)
        truth.insert(v)
    truth.finalize()
    return h, truth

def build_churn(rng, max_buckets, n_initial=10000, n_ops=30000):
    h = IncrementalHistogram(*DOMAIN, max_buckets=max_buckets)
    truth = Truth()
    live = []
    zipf_pool = gen_zipf(rng, n_initial + n_ops)
    pool_idx = 0

    def fresh_value():
        nonlocal pool_idx
        v = zipf_pool[pool_idx]
        pool_idx += 1
        return v

    for _ in range(n_initial):
        v = fresh_value()
        live.append(v); h.insert(v); truth.insert(v)
    for _ in range(n_ops):
        r = rng.random()
        if r < 0.45 or not live:
            v = fresh_value()
            live.append(v); h.insert(v); truth.insert(v)
        elif r < 0.90:
            i = rng.randrange(len(live))
            v = live.pop(i)
            h.delete(v); truth.delete(v)
        else:  # update = delete + insert
            i = rng.randrange(len(live))
            old = live[i]
            new = fresh_value()
            live[i] = new
            h.update(old, new)
            truth.delete(old); truth.insert(new)
    truth.finalize()
    return h, truth


# -------------------------------------------------------------- evaluation
def make_queries(rng, n_queries=300):
    queries = []
    for _ in range(n_queries):
        kind = rng.random()
        center = rng.uniform(*DOMAIN)
        if kind < 0.25:
            width = 1.0                      # near-point
        elif kind < 0.55:
            width = DOMAIN[1] * 0.01         # 1%
        elif kind < 0.85:
            width = DOMAIN[1] * 0.10         # 10%
        else:
            width = DOMAIN[1] * 0.50         # 50%
        a = max(DOMAIN[0], min(center - width / 2, DOMAIN[1] - width))
        queries.append((a, a + width))
    return queries

def evaluate(h, truth, queries):
    norm_errs, rel_errs = [], []
    for a, b in queries:
        est = h.estimate_range(a, b)
        true = truth.range_count(a, b)
        abs_err = abs(est - true)
        norm_errs.append(abs_err / max(truth.total, 1))
        if true > 0:
            rel_errs.append(abs_err / true)
    avg = sum(norm_errs) / len(norm_errs) if norm_errs else 0.0
    mx = max(norm_errs) if norm_errs else 0.0
    rel = sum(rel_errs) / len(rel_errs) if rel_errs else 0.0
    return avg, mx, rel


# ------------------------------------------------------------------ main
def main():
    rng = random.Random(42)
    queries = make_queries(rng)
    n = 20000

    print("=" * 78)
    print("1) Scenario comparison (max_buckets=32, %d rows, %d range queries)" % (n, len(queries)))
    print("=" * 78)
    print("%-14s %8s %6s | %10s %10s %10s" % ("scenario", "rows", "buckets",
          "avg|err|/N", "max|err|/N", "avg rel"))
    print("-" * 78)

    # empty table
    h = IncrementalHistogram(*DOMAIN, max_buckets=32)
    truth = Truth(); truth.finalize()
    avg, mx, rel = evaluate(h, truth, queries)
    print("%-14s %8d %6d | %10.5f %10.5f %10.5f" % ("empty", 0, len(h.buckets), avg, mx, rel))

    scenarios = [
        ("uniform", gen_uniform(rng, n)),
        ("concentrated", gen_concentrated(rng, n)),
        ("skewed(zipf)", gen_zipf(rng, n)),
    ]
    for name, values in scenarios:
        h, truth = build_static(name, values, max_buckets=32)
        avg, mx, rel = evaluate(h, truth, queries)
        print("%-14s %8d %6d | %10.5f %10.5f %10.5f" % (name, truth.total, len(h.buckets), avg, mx, rel))

    h, truth = build_churn(rng, max_buckets=32)
    avg, mx, rel = evaluate(h, truth, queries)
    print("%-14s %8d %6d | %10.5f %10.5f %10.5f" % ("churn", truth.total, len(h.buckets), avg, mx, rel))

    print()
    print("=" * 78)
    print("2) Bucket-capacity sweep on skewed(zipf) and churn workloads")
    print("=" * 78)
    print("%-12s | %8s %8s %8s %8s %8s %8s" % ("workload", "B=1", "B=4", "B=8", "B=16", "B=32", "B=64"))
    print("-" * 78)
    zipf_values = gen_zipf(rng, n)
    for label, builder in (("zipf avg", "zipf"), ("churn avg", "churn")):
        row = []
        for b in (1, 4, 8, 16, 32, 64):
            if builder == "zipf":
                h, truth = build_static("zipf", zipf_values, max_buckets=b)
            else:
                h, truth = build_churn(random.Random(42), max_buckets=b)
            avg, _, _ = evaluate(h, truth, queries)
            row.append(avg)
        print("%-12s | %s" % (label, " ".join("%8.5f" % e for e in row)))

    print()
    print("=" * 78)
    print("3) Bucket boundary evolution demo (max_buckets=8, domain [0,1000))")
    print("=" * 78)
    demo = IncrementalHistogram(0.0, 1000.0, max_buckets=8)
    demo_rng = random.Random(9)
    milestones = (50, 200, 1000, 4000)
    inserted = []
    for step in range(1, 4001):
        v = demo_rng.expovariate(1 / 150.0) % 1000.0  # skewed toward 0
        demo.insert(v)
        inserted.append(v)
        if step in milestones:
            print("after %5d inserts (%d buckets):" % (step, len(demo.buckets)))
            for lo, hi, c in demo.snapshot():
                print("    [%8.2f, %8.2f)  count=%d" % (lo, hi, c))
    print("deleting 3900 of 4000 rows ...")
    for v in inserted[:3900]:
        demo.delete(v)
    print("after deletes (%d buckets, total=%d, merges=%d):" % (len(demo.buckets), demo.total, demo.num_merges))
    for lo, hi, c in demo.snapshot():
        print("    [%8.2f, %8.2f)  count=%d" % (lo, hi, c))
    demo.check_invariants()
    print("invariants OK (splits=%d, merges=%d)" % (demo.num_splits, demo.num_merges))


if __name__ == "__main__":
    main()
