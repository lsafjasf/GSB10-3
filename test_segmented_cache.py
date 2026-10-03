"""Self-tests for SegmentedCache. Run: python3 -m unittest -v"""

import random
import threading
import unittest

from segmented_cache import SegmentedCache, stable_segment_index


def keys_for_segment(segment, count, segment_count=8):
    """Deterministically find `count` keys that all map to `segment`."""
    keys, i = [], 0
    while len(keys) < count:
        key = "probe-%d" % i
        if stable_segment_index(key, segment_count) == segment:
            keys.append(key)
        i += 1
    return keys


class TestMappingStability(unittest.TestCase):
    def test_same_key_same_segment_repeatedly(self):
        cache = SegmentedCache(capacity=64, segment_count=8)
        for key in ["alpha", 42, 3.14, ("t", 1), b"bytes"]:
            first = cache.segment_index(key)
            for _ in range(100):
                self.assertEqual(cache.segment_index(key), first)

    def test_mapping_independent_of_cache_instance_and_state(self):
        a = SegmentedCache(capacity=8, segment_count=16)
        b = SegmentedCache(capacity=10_000, segment_count=16)
        for i in range(200):
            a.put("k%d" % i, i)  # mutate a; mapping must not drift
        for i in range(200):
            key = "k%d" % i
            self.assertEqual(a.segment_index(key), b.segment_index(key))
            self.assertEqual(a.segment_index(key),
                             stable_segment_index(key, 16))

    def test_mapping_depends_only_on_segment_count(self):
        for n in (1, 2, 7, 16, 64):
            for i in range(50):
                idx = stable_segment_index("key-%d" % i, n)
                self.assertTrue(0 <= idx < n)

    def test_segments_actually_used(self):
        cache = SegmentedCache(capacity=10_000, segment_count=8)
        touched = {cache.segment_index("spread-%d" % i) for i in range(500)}
        self.assertGreater(len(touched), 1)  # not degenerate


class TestGlobalCapacityBound(unittest.TestCase):
    def test_bound_holds_under_heavy_insert(self):
        capacity = 100
        cache = SegmentedCache(capacity=capacity, segment_count=8)
        for i in range(2_000):
            cache.put("key-%d" % i, i)
            self.assertLessEqual(cache.size, capacity)
        stats = cache.stats()
        self.assertEqual(stats["size"], capacity)
        self.assertEqual(sum(s["size"] for s in stats["per_segment"]),
                         stats["size"])

    def test_no_per_segment_quota_hot_segment_may_exceed_share(self):
        # Global bound means segments have NO fixed quota of
        # capacity/segment_count; a hot segment may hold far more than its
        # "fair share" as long as the global sum stays within capacity.
        capacity, segment_count = 80, 8
        cache = SegmentedCache(capacity=capacity, segment_count=segment_count)
        hot_keys = keys_for_segment(0, capacity, segment_count)
        for key in hot_keys:
            cache.put(key, 1)
        hot_size = cache.stats()["per_segment"][0]["size"]
        self.assertGreater(hot_size, capacity // segment_count)
        self.assertLessEqual(cache.size, capacity)

    def test_fill_exact_capacity_all_hit(self):
        cache = SegmentedCache(capacity=50, segment_count=4)
        for i in range(50):
            cache.put(i, i * 10)
        for i in range(50):
            self.assertEqual(cache.get(i), i * 10)
        stats = cache.stats()
        self.assertEqual(stats["misses"], 0)
        self.assertEqual(stats["evictions"], 0)

    def test_one_past_capacity_evicts_exactly_one(self):
        cache = SegmentedCache(capacity=50, segment_count=4)
        for i in range(50):
            cache.put(i, i)
        cache.put("overflow", 1)
        stats = cache.stats()
        self.assertEqual(stats["size"], 50)
        self.assertEqual(stats["evictions"], 1)

    def test_overwrite_does_not_grow(self):
        cache = SegmentedCache(capacity=10, segment_count=2)
        for i in range(10):
            cache.put(i, i)
        for i in range(10):
            cache.put(i, -i)
        self.assertEqual(cache.size, 10)
        self.assertEqual(cache.stats()["evictions"], 0)
        for i in range(10):
            self.assertEqual(cache.get(i), -i)


class TestEdgeCases(unittest.TestCase):
    def test_capacity_one(self):
        cache = SegmentedCache(capacity=1, segment_count=4)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.size, 1)
        self.assertEqual(cache.stats()["evictions"], 1)

    def test_single_segment(self):
        cache = SegmentedCache(capacity=3, segment_count=1)
        for i in range(10):
            cache.put(i, i)
        self.assertEqual(cache.size, 3)
        self.assertEqual(cache.get(9), 9)
        self.assertIsNone(cache.get(0))

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            SegmentedCache(capacity=0)
        with self.assertRaises(ValueError):
            SegmentedCache(capacity=10, segment_count=0)

    def test_miss_returns_default_and_counts(self):
        cache = SegmentedCache(capacity=4, segment_count=2)
        self.assertEqual(cache.get("nope", "dflt"), "dflt")
        self.assertEqual(cache.stats()["misses"], 1)

    def test_lru_order_within_segment(self):
        cache = SegmentedCache(capacity=3, segment_count=1)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.get("a")            # a is now most-recently-used
        cache.put("d", 4)         # must evict b, not a
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("b"))


class TestAccessPatterns(unittest.TestCase):
    def test_single_key_repeated_access(self):
        cache = SegmentedCache(capacity=16, segment_count=8)
        cache.put("hot", "v")
        for _ in range(999):
            self.assertEqual(cache.get("hot"), "v")
        stats = cache.stats()
        self.assertEqual(stats["hits"], 999)
        self.assertEqual(stats["misses"], 0)
        self.assertAlmostEqual(stats["hit_rate"], 1.0)

    def test_uneven_cross_segment_pressure(self):
        # Hot keys pinned in segment 0, cold churn spread everywhere.
        capacity, segment_count = 128, 8
        cache = SegmentedCache(capacity=capacity, segment_count=segment_count)
        hot = keys_for_segment(0, 8, segment_count)
        for key in hot:
            cache.put(key, "hot")
        rng = random.Random(7)
        for i in range(5_000):
            cache.put("cold-%d" % i, i)
            for key in rng.sample(hot, 4):
                self.assertEqual(cache.get(key), "hot")
        stats = cache.stats()
        self.assertLessEqual(stats["size"], capacity)
        self.assertGreater(stats["hit_rate"], 0.9)
        for key in hot:  # hot set survived the churn
            self.assertIn(key, cache)


class TestConcurrency(unittest.TestCase):
    def test_concurrent_read_write_bound_and_consistency(self):
        capacity, segment_count = 256, 16
        cache = SegmentedCache(capacity=capacity, segment_count=segment_count)
        errors = []
        ops_per_thread = 4_000

        def worker(seed):
            rng = random.Random(seed)
            try:
                for i in range(ops_per_thread):
                    key = "k%d" % rng.randrange(2_000)
                    if rng.random() < 0.5:
                        cache.put(key, i)
                    else:
                        cache.get(key)
                    if cache.size > capacity:
                        errors.append("bound violated: %d" % cache.size)
            except Exception as exc:  # report, don't swallow
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(s,))
                   for s in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        stats = cache.stats()
        self.assertLessEqual(stats["size"], capacity)
        self.assertEqual(sum(s["size"] for s in stats["per_segment"]),
                         stats["size"])
        total_lookups = stats["hits"] + stats["misses"]
        self.assertGreater(total_lookups, 0)
        self.assertEqual(total_lookups,
                         sum(s["hits"] + s["misses"]
                             for s in stats["per_segment"]))

    def test_concurrent_same_key(self):
        cache = SegmentedCache(capacity=8, segment_count=4)

        def worker():
            for i in range(1_000):
                cache.put("shared", i)
                cache.get("shared")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(cache.size, 1)
        self.assertIn("shared", cache)


if __name__ == "__main__":
    unittest.main()
