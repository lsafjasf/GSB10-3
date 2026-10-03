"""Experiments: estimation error of IncrementalHistogram vs ground truth.

Scenarios (all required by the task):
  * empty table            - verified in the unit tests, sanity-checked here
  * concentrated           - Gaussian mass around the domain centre
  * highly skewed          - Zipf-like (1/i) head-heavy distribution
  * uniform                - baseline
  * churn                  - interleaved inserts/deletes (frequent updates)

For every scenario and every bucket budget M we measure, over random range
queries against the exact multiset:
  * mean absolute selectivity error  |sel_est - sel_true|   (sel = count/N)
  * mean relative error              |est-true| / max(true, 1)
  * 95th-percentile absolute selectivity error

Run:  python3 experiments.py
"""

import bisect
import random
from collections import Counter

from incremental_histogram import IncrementalHistogram

DOMAIN = (0, 10000)
N_ROWS = 20000
N_QUERIES = 2000
BUCKET_BUDGETS = [4, 8, 16, 32, 64]
SEED = 20261004


# ---------------------------------------------------------------- data ----

def gen_uniform(rng, n):
    return [rng.randrange(*DOMAIN) for _ in range(n)]


def gen_concentrated(rng, n):
    out = []
    lo, hi = DOMAIN
    while len(out) < n:
        v = int(rng.gauss(5000, 150))
        if lo <= v <= hi:
            out.append(v)
    return out


def gen_skewed(rng, n):
    lo, hi = DOMAIN
    weights = [1.0 / (i + 1) for i in range(lo, hi)]
    return rng.choices(range(lo, hi), weights=weights, k=n)


def apply_churn(rng, hist, counter, ops):
    """Interleave inserts/deletes; histogram and truth move together."""
    lo, hi = DOMAIN
    live = []
    for _ in range(ops):
        if not live or rng.random() < 0.55:
            v = rng.randrange(lo, hi)
            hist.insert(v)
            counter[v] += 1
            live.append(v)
        else:
            i = rng.randrange(len(live))
            v = live[i]
            live[i] = live[-1]
            live.pop()
            hist.delete(v)
            counter[v] -= 1
            if counter[v] == 0:
                del counter[v]


# ------------------------------------------------------------ evaluation ----

class Truth:
    """Exact multiset with O(log n) range counts via sorted keys."""

    def __init__(self, counter):
        self.keys = sorted(counter)
        self.prefix = [0]
        for k in self.keys:
            self.prefix.append(self.prefix[-1] + counter[k])
        self.total = self.prefix[-1]

    def range_count(self, lo, hi):
        l = bisect.bisect_left(self.keys, lo)
        r = bisect.bisect_left(self.keys, hi)
        return self.prefix[r] - self.prefix[l]


def evaluate(hist, truth, rng, n_queries):
    lo_dom, hi_dom = DOMAIN
    abs_sel_err, rel_err = [], []
    for _ in range(n_queries):
        a = rng.uniform(lo_dom, hi_dom)
        b = rng.uniform(lo_dom, hi_dom)
        lo, hi = min(a, b), max(a, b)
        est = hist.estimate(lo, hi)
        act = truth.range_count(lo, hi)
        if truth.total == 0:
            continue
        abs_sel_err.append(abs(est - act) / truth.total)
        rel_err.append(abs(est - act) / max(act, 1))
    abs_sel_err.sort()
    p95 = abs_sel_err[int(0.95 * (len(abs_sel_err) - 1))]
    return (
        sum(abs_sel_err) / len(abs_sel_err),
        sum(rel_err) / len(rel_err),
        p95,
    )


def run_scenario(name, rows, max_buckets, churn_ops=0):
    rng = random.Random(SEED)
    hist = IncrementalHistogram(*DOMAIN, max_buckets=max_buckets)
    counter = Counter()
    for v in rows:
        hist.insert(v)
        counter[v] += 1
    if churn_ops:
        apply_churn(rng, hist, counter, churn_ops)
    truth = Truth(counter)
    assert hist.total == truth.total, "histogram lost tuples!"
    q_rng = random.Random(SEED + 1)
    mae, mre, p95 = evaluate(hist, truth, q_rng, N_QUERIES)
    splits = sum(1 for e in hist.events if e.startswith("split"))
    merges = sum(1 for e in hist.events if e.startswith("merge"))
    print(
        f"{name:<13} M={max_buckets:<3} N={truth.total:<6} "
        f"buckets={len(hist):<3} splits={splits:<5} merges={merges:<5} "
        f"MAE(sel)={mae:.5f}  P95(sel)={p95:.5f}  meanRelErr={mre:.4f}"
    )
    return mae


def main():
    rng = random.Random(SEED)
    datasets = {
        "uniform": gen_uniform(rng, N_ROWS),
        "concentrated": gen_concentrated(rng, N_ROWS),
        "skewed(zipf)": gen_skewed(rng, N_ROWS),
    }

    print("== estimation error vs bucket budget (N up to %d rows, %d queries) =="
          % (N_ROWS, N_QUERIES))
    for name, rows in datasets.items():
        for m in BUCKET_BUDGETS:
            run_scenario(name, rows, m)

    print("\n== churn workload: 40k interleaved inserts/deletes after load ==")
    for name, rows in datasets.items():
        for m in (8, 16, 32):
            run_scenario(name + "+churn", rows, m, churn_ops=40000)

    print("\n== empty table ==")
    h = IncrementalHistogram(*DOMAIN, max_buckets=16)
    print(f"total={h.total} buckets={len(h)} estimate(0,10000)={h.estimate(0, 10000)}")

    print("\n== bucket evolution sample (concentrated data, M=8) ==")
    h = IncrementalHistogram(*DOMAIN, max_buckets=8)
    trace_rng = random.Random(7)
    rows = [min(9999, max(0, int(trace_rng.gauss(5000, 300)))) for _ in range(4000)]
    for v in rows:
        h.insert(v)
    print("-- first 8 structural events during load --")
    for e in h.events[:8]:
        print("  " + e)
    print("-- final buckets --")
    for b in h.buckets():
        print(f"  [{b.lo:9.2f}, {b.hi:9.2f})  count={b.count}")
    print("-- deleting 90% of the rows triggers merges --")
    for v in rows[: int(0.9 * len(rows))]:
        h.delete(v)
    for e in h.events[-6:]:
        print("  " + e)
    print("-- buckets after deletes --")
    for b in h.buckets():
        print(f"  [{b.lo:9.2f}, {b.hi:9.2f})  count={b.count}")


if __name__ == "__main__":
    main()
