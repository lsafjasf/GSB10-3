"""耗时基准：展示分治 O(n log n) 与暴力 O(n^2) 的差异。

运行：python3 benchmark.py
同一随机序列生成点集，每组重复 3 次取最小值。
"""

import random
import time

from closest_pair import closest_pairs, closest_pairs_bruteforce


def time_it(fn, pts, repeats=3):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(pts)
        best = min(best, time.perf_counter() - t0)
    return best


def main():
    rng = random.Random(777)

    print("== 分治 closest_pairs（O(n log n)）==")
    print(f"{'n':>8} {'耗时(s)':>12} {'n*log2(n)':>14} "
          f"{'us / (n*log2n)':>16} {'翻倍倍速':>10}")
    import math
    prev_t = None
    for n in [1000, 2000, 4000, 8000, 16000, 32000, 64000, 100000]:
        pts = [(rng.uniform(0, 1_000_000), rng.uniform(0, 1_000_000))
               for _ in range(n)]
        t = time_it(closest_pairs, pts)
        scale = n * math.log2(n)
        ratio = t / scale * 1e6
        speedup = f"{t / prev_t:.2f}x" if prev_t else "-"
        print(f"{n:>8} {t:>12.4f} {scale:>14.0f} {ratio:>16.3f} {speedup:>10}")
        prev_t = t

    print()
    print("== 暴力 closest_pairs_bruteforce（O(n^2)，小规模对照）==")
    print(f"{'n':>8} {'耗时(s)':>12} {'us / n^2':>12} {'翻倍倍速':>10}")
    prev_t = None
    for n in [1000, 2000, 4000, 8000]:
        pts = [(rng.uniform(0, 1_000_000), rng.uniform(0, 1_000_000))
               for _ in range(n)]
        t = time_it(closest_pairs_bruteforce, pts, repeats=1)
        ratio = t / (n * n) * 1e6
        speedup = f"{t / prev_t:.2f}x" if prev_t else "-"
        print(f"{n:>8} {t:>12.4f} {ratio:>12.3f} {speedup:>10}")
        prev_t = t

    print()
    print("== 外推对比（n=100000）==")
    pts = [(rng.uniform(0, 1_000_000), rng.uniform(0, 1_000_000)) for _ in range(100000)]
    t_fast = time_it(closest_pairs, pts)
    per_pair = None
    pts2 = [(rng.uniform(0, 1_000_000), rng.uniform(0, 1_000_000)) for _ in range(8000)]
    t8k = time_it(closest_pairs_bruteforce, pts2, repeats=1)
    per_pair = t8k / (8000 * 7999 / 2)
    est_slow = per_pair * (100000 * 99999 / 2)
    print(f"分治实测:        {t_fast:8.3f} s")
    print(f"暴力按实测外推:  {est_slow:8.1f} s "
          f"（约 {est_slow / 3600:.1f} 小时，约 {est_slow / t_fast:.0f} 倍）")


if __name__ == "__main__":
    main()
