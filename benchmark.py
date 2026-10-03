"""Hit-rate benchmark for SegmentedCache. Run: python3 benchmark.py

Reports global hit rate (eviction is segment-local, stats are global) for
several segment counts under a skewed workload, plus a concurrent run.
"""

import random
import threading
import time

from segmented_cache import SegmentedCache


def zipf_workload(cache, key_space, ops, seed=1):
    """80/20-style skew: a small hot subset gets most of the traffic."""
    rng = random.Random(seed)
    hot = ["key-%d" % rng.randrange(key_space) for _ in range(key_space // 10)]
    for _ in range(ops):
        key = rng.choice(hot) if rng.random() < 0.8 else \
            "key-%d" % rng.randrange(key_space)
        if rng.random() < 0.7:
            cache.get(key)
        else:
            cache.put(key, 1)


def bench_segments():
    print("== skewed workload, capacity=1024, key_space=4096, 200k ops ==")
    print("%-10s %-10s %-10s %-10s" % ("segments", "hit_rate", "evictions",
                                       "size"))
    for segment_count in (1, 4, 16, 64):
        cache = SegmentedCache(capacity=1024, segment_count=segment_count)
        zipf_workload(cache, key_space=4096, ops=200_000)
        s = cache.stats()
        print("%-10d %-10.4f %-10d %-10d"
              % (segment_count, s["hit_rate"], s["evictions"], s["size"]))


def bench_concurrent():
    print("\n== concurrent: 8 threads x 25k ops, capacity=2048 ==")
    print("%-10s %-10s %-12s %-10s" % ("segments", "hit_rate", "ops/sec",
                                       "size"))
    for segment_count in (1, 16, 64):
        cache = SegmentedCache(capacity=2048, segment_count=segment_count)

        def worker(seed):
            rng = random.Random(seed)
            for _ in range(25_000):
                key = "k%d" % rng.randrange(4096)
                if rng.random() < 0.7:
                    cache.get(key)
                else:
                    cache.put(key, 1)

        threads = [threading.Thread(target=worker, args=(s,))
                   for s in range(8)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start
        s = cache.stats()
        print("%-10d %-10.4f %-12.0f %-10d"
              % (segment_count, s["hit_rate"], 200_000 / elapsed, s["size"]))
        assert s["size"] <= s["capacity"], "global bound violated"


if __name__ == "__main__":
    bench_segments()
    bench_concurrent()
