"""分段缓存自测（标准库 unittest，可直接 python3 test_segmented_cache.py 运行）。"""

import random
import string
import subprocess
import sys
import threading
import unittest

from segmented_cache import (
    SegmentedCache,
    assert_mapping_stability,
    shard_index,
    stable_hash,
)


def make_keys(n, prefix="key"):
    return [f"{prefix}-{i}" for i in range(n)]


def fill_one_shard(cache, shard_id, keys):
    """返回全部落在指定段上的键（用于构造跨段压力不均的场景）。"""
    picked = []
    i = 0
    while len(picked) < keys:
        k = f"probe-{i}"
        if cache.shard_for_key(k) == shard_id:
            picked.append(k)
        i += 1
    return picked


class TestMappingStability(unittest.TestCase):
    def test_same_key_same_shard_repeated(self):
        keys = make_keys(2000)
        for n in (1, 2, 7, 16, 64):
            assert_mapping_stability(keys, n, rounds=5)

    def test_mapping_is_pure_function_of_key(self):
        # 与缓存实例无关：不同实例、不同容量，同键必同段
        c1 = SegmentedCache(capacity=10, num_shards=16)
        c2 = SegmentedCache(capacity=99999, num_shards=16)
        for k in make_keys(500):
            self.assertEqual(c1.shard_for_key(k), c2.shard_for_key(k))
            self.assertEqual(c1.shard_for_key(k), shard_index(k, 16))

    def test_hash_stable_across_processes(self):
        # 不依赖 PYTHONHASHSEED：子进程（不同随机种子）算出的段号必须一致
        keys = ["alpha", "beta", "gamma", "键-中文", "", "x" * 1000]
        expected = [shard_index(k, 16) for k in keys]
        code = (
            "from segmented_cache import shard_index;"
            f"keys={keys!r};"
            "print([shard_index(k,16) for k in keys])"
        )
        for seed in ("0", "42", "12345"):
            env = {"PYTHONHASHSEED": seed, "PYTHONPATH": "."}
            out = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, env=env, check=True,
            )
            self.assertEqual(eval(out.stdout.strip()), expected)

    def test_distribution_not_degenerate(self):
        # 分布健全性：2000 个键落在 16 段，每段占比应在 [1/32, 3/16] 内
        n, num_shards = 2000, 16
        counts = [0] * num_shards
        for k in make_keys(n):
            counts[shard_index(k, num_shards)] += 1
        for c in counts:
            self.assertGreater(c, n / num_shards / 2)
            self.assertLess(c, n / num_shards * 1.5)


class TestCapacityBound(unittest.TestCase):
    def test_quota_sum_equals_global_capacity(self):
        # 各段配额之和恰好等于全局容量（余数分给前面的段）
        for capacity, num_shards in ((100, 16), (97, 16), (16, 16), (3, 8), (1, 4), (10, 3)):
            cache = SegmentedCache(capacity=capacity, num_shards=num_shards)
            self.assertEqual(sum(cache.quotas), capacity)
            base, extra = divmod(capacity, num_shards)
            expected = [base + (1 if i < extra else 0) for i in range(num_shards)]
            self.assertEqual(cache.quotas, expected)

    def test_global_bound_under_uniform_load(self):
        cache = SegmentedCache(capacity=100, num_shards=16)
        for k in make_keys(500):  # 远超容量地灌入
            cache.set(k, 1)
            cache.assert_capacity_invariant()
        self.assertEqual(len(cache), 100)

    def test_global_bound_under_skewed_load(self):
        # 跨段压力不均：全部键都打在一个段上，整表上界仍必须成立
        cache = SegmentedCache(capacity=64, num_shards=16)
        hot_keys = fill_one_shard(cache, 3, 500)
        for k in hot_keys:
            cache.set(k, 1)
            cache.assert_capacity_invariant()
        self.assertEqual(len(cache), 64)
        # 且热点段可以借用其他段的空闲配额（> 自己的公平份额 4）
        self.assertGreater(cache.stats()["per_shard"][3]["size"], 64 // 16)

    def test_quota_is_not_a_hard_cap(self):
        # 与“按段简单分配”的本质区别：单段可超过自身配额，只要整表没满
        cache = SegmentedCache(capacity=64, num_shards=16)  # 每段配额 4
        hot = fill_one_shard(cache, 0, 30)
        for k in hot:
            cache.set(k, 1)
        self.assertEqual(cache.stats()["per_shard"][0]["size"], 30)
        self.assertEqual(len(cache), 30)

    def test_num_shards_greater_than_capacity(self):
        cache = SegmentedCache(capacity=3, num_shards=8)  # 多数段配额为 0
        self.assertEqual(sum(cache.quotas), 3)
        for k in make_keys(50):
            cache.set(k, 1)
            cache.assert_capacity_invariant()
        self.assertEqual(len(cache), 3)

    def test_capacity_one(self):
        cache = SegmentedCache(capacity=1, num_shards=4)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.assert_capacity_invariant()
        self.assertEqual(len(cache), 1)
        self.assertNotIn("a", cache)
        self.assertEqual(cache.get("b"), 2)


class TestEvictionSemantics(unittest.TestCase):
    def test_lru_within_shard(self):
        # 段内 LRU：同段键灌满后，最久未用的先被淘汰
        cache = SegmentedCache(capacity=8, num_shards=1)  # 单段便于验证
        for k in make_keys(8):
            cache.set(k, 1)
        cache.get("key-0")  # 让 key-0 变热
        cache.set("key-8", 1)  # 应淘汰 key-1 而不是 key-0
        self.assertIn("key-0", cache)
        self.assertNotIn("key-1", cache)
        self.assertIn("key-8", cache)

    def test_update_refreshes_lru_and_keeps_size(self):
        cache = SegmentedCache(capacity=4, num_shards=1)
        for k in make_keys(4):
            cache.set(k, 1)
        cache.set("key-0", 99)  # 更新已存在的键：刷新 LRU、不占新容量
        self.assertEqual(len(cache), 4)
        cache.set("key-4", 1)  # 应淘汰 key-1
        self.assertIn("key-0", cache)
        self.assertEqual(cache.get("key-0"), 99)
        self.assertNotIn("key-1", cache)

    def test_remote_eviction_when_target_shard_empty(self):
        # 极端情形：整表已满、目标段为空 -> 从当前最大段淘汰其 LRU 尾部
        cache = SegmentedCache(capacity=8, num_shards=16)
        hot = fill_one_shard(cache, 5, 8)  # 8 个键全在段 5，恰好填满整表
        for k in hot:
            cache.set(k, 1)
        self.assertEqual(len(cache), 8)
        outsider = next(k for k in make_keys(1000, "out")
                        if cache.shard_for_key(k) != 5)
        cache.set(outsider, 2)
        cache.assert_capacity_invariant()
        self.assertEqual(len(cache), 8)
        self.assertIn(outsider, cache)
        self.assertNotIn(hot[0], cache)  # 段 5 的 LRU 尾部被淘汰
        self.assertIn(hot[-1], cache)

    def test_single_key_repeated_access(self):
        cache = SegmentedCache(capacity=16, num_shards=4)
        for _ in range(10000):
            cache.set("hot", 1)
            self.assertEqual(cache.get("hot"), 1)
        self.assertEqual(len(cache), 1)
        stats = cache.stats()
        self.assertEqual(stats["total_hits"], 10000)
        self.assertEqual(stats["global_hit_rate"], 1.0)

    def test_delete_and_clear(self):
        cache = SegmentedCache(capacity=10, num_shards=4)
        for k in make_keys(10):
            cache.set(k, 1)
        self.assertTrue(cache.delete("key-0"))
        self.assertFalse(cache.delete("key-0"))
        self.assertEqual(len(cache), 9)
        cache.assert_capacity_invariant()
        cache.clear()
        self.assertEqual(len(cache), 0)
        for k in make_keys(10):
            self.assertNotIn(k, cache)

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            SegmentedCache(capacity=0)
        with self.assertRaises(ValueError):
            SegmentedCache(capacity=10, num_shards=0)
        with self.assertRaises(TypeError):
            SegmentedCache(capacity=4).set(123, 1)  # 非字符串键


class TestConcurrency(unittest.TestCase):
    def test_concurrent_read_write_invariants(self):
        capacity, num_shards = 500, 16
        cache = SegmentedCache(capacity=capacity, num_shards=num_shards)
        keys = make_keys(2000)
        errors = []

        def writer(seed):
            rng = random.Random(seed)
            try:
                for _ in range(5000):
                    k = rng.choice(keys)
                    if rng.random() < 0.1:
                        cache.delete(k)
                    else:
                        cache.set(k, k)  # 值恒等于键，便于校验一致性
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def reader(seed):
            rng = random.Random(seed)
            try:
                for _ in range(5000):
                    k = rng.choice(keys)
                    v = cache.get(k)
                    if v is not None and v != k:
                        errors.append(ValueError(f"torn value: {k!r} -> {v!r}"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(i,)) for i in range(4)
        ] + [
            threading.Thread(target=reader, args=(100 + i,)) for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        cache.assert_capacity_invariant()
        # 缓存中所有现存键值对必须一致（无撕裂写）
        for k in keys:
            if k in cache:
                self.assertEqual(cache.get(k), k)

    def test_concurrent_same_key(self):
        cache = SegmentedCache(capacity=8, num_shards=4)

        def hammer(i):
            for n in range(2000):
                cache.set("shared", (i, n))
                cache.get("shared")

        threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(cache), 1)
        cache.assert_capacity_invariant()


class TestStats(unittest.TestCase):
    def test_global_and_per_shard_hit_rates(self):
        cache = SegmentedCache(capacity=64, num_shards=4)
        for k in make_keys(64):
            cache.set(k, 1)
        for k in make_keys(64):  # 全命中
            cache.get(k)
        for k in make_keys(36, "miss"):  # 全未命中
            cache.get(k)
        stats = cache.stats()
        self.assertEqual(stats["total_hits"], 64)
        self.assertEqual(stats["total_misses"], 36)
        self.assertAlmostEqual(stats["global_hit_rate"], 0.64)
        # 全局命中率 = 各段命中率按请求数加权平均
        weighted = sum(
            s["hit_rate"] * (s["hits"] + s["misses"])
            for s in stats["per_shard"]
        ) / 100
        self.assertAlmostEqual(weighted, stats["global_hit_rate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
