"""Unit and boundary-case tests for the incremental histogram.

Run:  python3 test_incremental_histogram.py
"""

import random
import sys
import unittest

sys.path.insert(0, "src")
from incremental_histogram import IncrementalHistogram


class TestEmptyAndSingle(unittest.TestCase):
    def test_empty_table(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        self.assertEqual(h.total, 0)
        self.assertEqual(h.estimate_range(0, 100), 0.0)
        self.assertEqual(h.estimate_range(10, 20), 0.0)
        self.assertEqual(h.selectivity(0, 50), 0.0)
        h.check_invariants()

    def test_single_row(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        h.insert(42)
        self.assertEqual(h.total, 1)
        self.assertAlmostEqual(h.estimate_range(0, 100), 1.0)
        h.check_invariants()

    def test_delete_back_to_empty(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        for v in (10, 20, 30):
            h.insert(v)
        for v in (10, 20, 30):
            h.delete(v)
        self.assertEqual(h.total, 0)
        self.assertEqual(h.estimate_range(0, 100), 0.0)
        h.check_invariants()


class TestBoundaries(unittest.TestCase):
    def test_domain_edges_accepted(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        h.insert(0)      # exact lower bound
        h.insert(100)    # exact upper bound (last bucket is closed)
        self.assertEqual(h.total, 2)
        h.check_invariants()

    def test_out_of_domain_rejected(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        for bad in (-1, 100.5, 10**9):
            with self.assertRaises(ValueError):
                h.insert(bad)
            with self.assertRaises(ValueError):
                h.delete(bad)

    def test_invalid_construction(self):
        with self.assertRaises(ValueError):
            IncrementalHistogram(100, 0)          # low >= high
        with self.assertRaises(ValueError):
            IncrementalHistogram(0, 100, max_buckets=0)
        with self.assertRaises(ValueError):
            IncrementalHistogram(0, 100, split_factor=1.0)

    def test_delete_from_empty_histogram_raises(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        with self.assertRaises(ValueError):
            h.delete(50)  # nothing was ever inserted

    def test_query_outside_domain_clamped(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        for i in range(1000):
            h.insert(i / 10.0)
        self.assertAlmostEqual(h.estimate_range(-50, 500), 1000.0)
        self.assertEqual(h.estimate_range(200, 300), 0.0)
        self.assertEqual(h.estimate_range(50, 50), 0.0)  # empty range


class TestSplitMerge(unittest.TestCase):
    def test_capacity_bound_never_exceeded(self):
        rng = random.Random(7)
        h = IncrementalHistogram(0, 1000, max_buckets=16)
        for _ in range(20000):
            h.insert(rng.random() * 1000)
            h.check_invariants()
        self.assertLessEqual(len(h.buckets), 16)
        self.assertGreater(h.num_splits, 0)

    def test_splits_happen_on_concentrated_data(self):
        h = IncrementalHistogram(0, 1000, max_buckets=8)
        for _ in range(5000):
            h.insert(500.0)  # single hot point
        self.assertGreater(len(h.buckets), 1)
        self.assertLessEqual(len(h.buckets), 8)
        h.check_invariants()

    def test_merges_happen_after_deletes(self):
        rng = random.Random(3)
        h = IncrementalHistogram(0, 1000, max_buckets=16)
        values = [rng.random() * 1000 for _ in range(8000)]
        for v in values:
            h.insert(v)
        buckets_before = len(h.buckets)
        for v in values[:7000]:  # delete most rows
            h.delete(v)
        h.check_invariants()
        self.assertGreater(h.num_merges, 0)
        self.assertLess(len(h.buckets), buckets_before)

    def test_count_conservation_under_churn(self):
        rng = random.Random(11)
        h = IncrementalHistogram(0, 100, max_buckets=12)
        live = []
        for _ in range(5000):
            if live and rng.random() < 0.5:
                v = live.pop(rng.randrange(len(live)))
                h.delete(v)
            else:
                v = rng.random() * 100
                live.append(v)
                h.insert(v)
            h.check_invariants()
        self.assertEqual(h.total, len(live))

    def test_full_range_estimate_equals_total(self):
        rng = random.Random(5)
        h = IncrementalHistogram(0, 1000, max_buckets=32)
        for _ in range(10000):
            h.insert(rng.expovariate(1 / 100.0) % 1000)
        self.assertAlmostEqual(h.estimate_range(0, 1000), h.total, places=6)

    def test_update_moves_row(self):
        h = IncrementalHistogram(0, 100, max_buckets=8)
        h.insert(10)
        h.update(10, 90)
        self.assertEqual(h.total, 1)
        h.check_invariants()


class TestAccuracySanity(unittest.TestCase):
    def test_uniform_data_low_error(self):
        rng = random.Random(1)
        h = IncrementalHistogram(0, 1000, max_buckets=32)
        n = 20000
        for _ in range(n):
            h.insert(rng.random() * 1000)
        # 10% range query should be close to 10% of rows
        est = h.estimate_range(300, 400)
        self.assertLess(abs(est - n * 0.1) / n, 0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
