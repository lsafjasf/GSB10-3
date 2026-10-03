"""Self-tests for IncrementalHistogram (standard library unittest only).

Covers: empty table, concentrated distribution, highly skewed data,
frequent insert/delete churn, and boundary/edge cases.
"""

import random
import unittest
from collections import Counter

from incremental_histogram import IncrementalHistogram


def actual_range(counter: Counter, lo: float, hi: float) -> int:
    return sum(c for v, c in counter.items() if lo <= v < hi)


class EmptyTableTests(unittest.TestCase):
    def test_empty_histogram(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        self.assertEqual(h.total, 0)
        self.assertEqual(len(h), 1)
        self.assertEqual(h.estimate(0, 100), 0.0)
        self.assertEqual(h.estimate(10, 20), 0.0)
        self.assertEqual(h.estimate_point(50), 0.0)

    def test_delete_from_empty_raises(self):
        h = IncrementalHistogram(0, 100)
        with self.assertRaises(KeyError):
            h.delete(5)

    def test_insert_delete_back_to_empty(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        for v in range(100):
            h.insert(v)
        for v in range(100):
            h.delete(v)
        self.assertEqual(h.total, 0)
        self.assertEqual(len(h), 1)  # collapsed back to a single bucket
        self.assertEqual(h.estimate(0, 100), 0.0)


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.h = IncrementalHistogram(0, 1000, max_buckets=8)

    def test_domain_endpoints_accepted(self):
        self.h.insert(0)
        self.h.insert(1000)
        self.assertEqual(self.h.total, 2)
        self.assertEqual(self.h.estimate(0, 1000), 2.0)

    def test_out_of_domain_rejected(self):
        for bad in (-1, 1001, -0.5):
            with self.assertRaises(ValueError):
                self.h.insert(bad)
            with self.assertRaises(ValueError):
                self.h.delete(bad)

    def test_query_outside_domain_is_zero(self):
        for v in range(0, 1000, 10):
            self.h.insert(v)
        self.assertEqual(self.h.estimate(-500, -1), 0.0)
        self.assertEqual(self.h.estimate(1001, 2000), 0.0)
        self.assertEqual(self.h.estimate(50, 50), 0.0)  # empty range
        self.assertEqual(self.h.estimate(60, 50), 0.0)  # inverted range

    def test_query_clamped_to_domain(self):
        for v in range(1000):
            self.h.insert(v)
        # Full-domain query must be exact regardless of clamping.
        self.assertEqual(self.h.estimate(-10, 5000), 1000.0)

    def test_delete_more_than_present_raises(self):
        self.h.insert(7, multiplicity=2)
        with self.assertRaises(KeyError):
            self.h.delete(7, multiplicity=3)
        self.h.delete(7, multiplicity=2)
        self.assertEqual(self.h.total, 0)

    def test_invalid_constructor_and_multiplicity(self):
        with self.assertRaises(ValueError):
            IncrementalHistogram(10, 10)
        with self.assertRaises(ValueError):
            IncrementalHistogram(0, 10, max_buckets=1)
        with self.assertRaises(ValueError):
            self.h.insert(5, multiplicity=0)
        with self.assertRaises(ValueError):
            self.h.delete(5, multiplicity=-1)

    def test_capacity_never_exceeded(self):
        rng = random.Random(7)
        for m in (2, 3, 5, 16):
            h = IncrementalHistogram(0, 1000, max_buckets=m)
            for _ in range(3000):
                h.insert(rng.uniform(0, 1000))
            self.assertLessEqual(len(h), m)
            self.assertEqual(h.total, 3000)

    def test_single_distinct_value_cannot_split_forever(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        for _ in range(5000):
            h.insert(42)
        self.assertEqual(h.total, 5000)
        self.assertLessEqual(len(h), 8)
        self.assertEqual(h.estimate(0, 100), 5000.0)


class ConcentratedDistributionTests(unittest.TestCase):
    def test_concentrated_data(self):
        rng = random.Random(11)
        h = IncrementalHistogram(0, 10000, max_buckets=32)
        counter = Counter()
        for _ in range(20000):
            v = min(9999, max(0, int(rng.gauss(5000, 100))))
            h.insert(v)
            counter[v] += 1
        self.assertEqual(h.total, 20000)
        self.assertLessEqual(len(h), 32)
        # Hot region should be estimated well (buckets are narrow there).
        est = h.estimate(4900, 5100)
        act = actual_range(counter, 4900, 5100)
        self.assertAlmostEqual(est, act, delta=0.10 * act)
        # Full range is always exact.
        self.assertEqual(h.estimate(0, 10000), 20000.0)


class SkewedDistributionTests(unittest.TestCase):
    def test_zipf_like_skew(self):
        rng = random.Random(23)
        h = IncrementalHistogram(0, 10000, max_buckets=32)
        counter = Counter()
        weights = [1.0 / (i + 1) for i in range(10000)]
        for _ in range(20000):
            v = rng.choices(range(10000), weights=weights, k=1)[0]
            h.insert(v)
            counter[v] += 1
        self.assertEqual(h.total, 20000)
        self.assertLessEqual(len(h), 32)
        # The head of the distribution carries most of the mass; the
        # histogram must track it reasonably.
        est = h.estimate(0, 100)
        act = actual_range(counter, 0, 100)
        self.assertGreater(act, 5000)  # sanity: really skewed
        self.assertAlmostEqual(est, act, delta=0.15 * act)

    def test_bucket_count_reflects_skew(self):
        rng = random.Random(5)
        h = IncrementalHistogram(0, 10000, max_buckets=16)
        for _ in range(10000):
            h.insert(rng.choices(range(10000), weights=[1.0 / (i + 1) for i in range(10000)])[0])
        counts = [b.count for b in h.buckets()]
        # Equi-depth maintenance: no bucket should dwarf the ideal share.
        ideal = h.total / 16
        self.assertLess(max(counts), 3 * ideal)


class ChurnTests(unittest.TestCase):
    def test_frequent_insert_delete(self):
        rng = random.Random(99)
        h = IncrementalHistogram(0, 1000, max_buckets=16)
        counter = Counter()
        for _ in range(30000):
            if rng.random() < 0.55 or not counter:
                v = rng.randrange(1000)
                h.insert(v)
                counter[v] += 1
            else:
                v = rng.choice(list(counter))
                h.delete(v)
                counter[v] -= 1
                if counter[v] == 0:
                    del counter[v]
            self.assertLessEqual(len(h), 16)
        self.assertEqual(h.total, sum(counter.values()))
        # Estimates stay consistent with the true multiset after churn.
        for _ in range(200):
            lo = rng.randrange(0, 900)
            hi = lo + rng.randrange(1, 100)
            est = h.estimate(lo, hi)
            act = actual_range(counter, lo, hi)
            self.assertAlmostEqual(est, act, delta=max(0.25 * act, 0.08 * h.total))

    def test_total_always_consistent(self):
        rng = random.Random(3)
        h = IncrementalHistogram(0, 100, max_buckets=4)
        live = Counter()
        for _ in range(5000):
            v = rng.randrange(100)
            if live and rng.random() < 0.5:
                victim = rng.choice(list(live))
                h.delete(victim)
                live[victim] -= 1
                if live[victim] == 0:
                    del live[victim]
            else:
                h.insert(v)
                live[v] += 1
            self.assertEqual(h.total, sum(live.values()))
            self.assertEqual(sum(b.count for b in h.buckets()), h.total)


class EstimationSemanticsTests(unittest.TestCase):
    def test_full_range_exact(self):
        rng = random.Random(1)
        h = IncrementalHistogram(0, 500, max_buckets=8)
        for _ in range(2000):
            h.insert(rng.uniform(0, 500))
        self.assertEqual(h.estimate(0, 500), 2000.0)

    def test_uniform_data_estimates_well(self):
        rng = random.Random(2)
        h = IncrementalHistogram(0, 1000, max_buckets=16)
        counter = Counter()
        for _ in range(10000):
            v = rng.randrange(1000)
            h.insert(v)
            counter[v] += 1
        est = h.estimate(200, 400)
        act = actual_range(counter, 200, 400)
        self.assertAlmostEqual(est, act, delta=0.05 * act)

    def test_split_and_merge_events_logged(self):
        rng = random.Random(8)
        h = IncrementalHistogram(0, 1000, max_buckets=8)
        for _ in range(3000):
            h.insert(rng.gauss(500, 50))
        self.assertTrue(any(e.startswith("split") for e in h.events))
        # Force merges via deletes.
        inserted_live = list(range(0, 1000, 2))
        for v in inserted_live:
            h.insert(v)
        for v in inserted_live:
            h.delete(v)
        self.assertTrue(any(e.startswith("merge") for e in h.events))

    def test_bucket_boundaries_fixed_and_ordered(self):
        rng = random.Random(4)
        h = IncrementalHistogram(0, 1000, max_buckets=10)
        for _ in range(5000):
            h.insert(rng.uniform(0, 1000))
        edges = h.edges()
        self.assertEqual(edges[0], 0.0)
        self.assertEqual(edges[-1], 1000.0)
        self.assertEqual(list(edges), sorted(edges))
        for b, lo, hi in zip(h.buckets(), edges, edges[1:]):
            self.assertEqual((b.lo, b.hi), (lo, hi))


if __name__ == "__main__":
    unittest.main(verbosity=2)
