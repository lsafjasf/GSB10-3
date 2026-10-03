"""最近点对耗时基准：分治 vs 暴力，并顺带对拍校验。

运行：python3 benchmark.py
随机点使用固定种子，数据可复现。
"""

import random
import time

from closest_pair import brute_force, closest_pair

NS = [1000, 2000, 5000, 10000, 20000, 50000, 100000]
BRUTE_NS = [500, 1000, 2000]
REPEAT = 3


def timeit(fn, *args, repeat=REPEAT):
    best = float("inf")
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn(*args)
        best = min(best, time.perf_counter() - t0)
    return best, result


def main():
    rng = random.Random(42)

    print("## 1. 随机浮点点（x,y ~ U(0,1)），分治耗时")
    print(f"{'n':>8} {'最快(s)':>10} {'t/(n log2 n)':>14} "
          f"{'最近距离':>13} {'点对数':>7} {'对拍':>5}  规模放大(实测/理论)")
    prev_n = prev_t = None
    for n in NS:
        pts = [(rng.random(), rng.random()) for _ in range(n)]
        t, r = timeit(closest_pair, pts)
        mark = ""
        if n <= 2000:
            mark = "OK" if r == brute_force(pts) else "BAD"
        scale = ""
        if prev_n is not None:
            expected = (n / prev_n) * (
                (n.bit_length() - 1) / (prev_n.bit_length() - 1))
            scale = f"{t / prev_t:>5.2f}x / {expected:.2f}x"
        print(f"{n:>8} {t:>10.4f} {t / (n * (n.bit_length() - 1)) * 1e6:>11.2f}us"
              f" {r.distance:>13.8g} {len(r.pairs):>7} {mark:>5}  {scale}")
        prev_n, prev_t = n, t

    print("\n## 2. 暴力法耗时对照（同样数据）")
    print(f"{'n':>8} {'暴力(s)':>10} {'分治(s)':>10} {'加速比':>8}")
    tb = None
    for n in BRUTE_NS:
        pts = [(rng.random(), rng.random()) for _ in range(n)]
        tb, rb = timeit(brute_force, pts, repeat=1)
        td, rd = timeit(closest_pair, pts)
        assert rb == rd
        print(f"{n:>8} {tb:>10.4f} {td:>10.4f} {tb / td:>7.1f}x")
    print(f"暴力法 n=2000 需 {tb:.2f}s，按 O(n^2) 外推 n=100000 约需 "
          f"{tb * (100000 / 2000) ** 2:.0f}s（约 {tb * 2500 / 3600:.1f} 小时），不再实测。")

    print("\n## 3. 退化情形：10 万个完全重合的点")
    pts = [(1.5, -2.5)] * 100000
    t, r = timeit(closest_pair, pts, repeat=1)
    print(f"n=100000 距离={r.distance} 组数={len(r.groups)} "
          f"组大小={len(r.groups[0])} 隐含点对数=C(100000,2)="
          f"{100000 * 99999 // 2} 耗时={t:.4f}s")

    print("\n## 4. 最坏形状：全部共线 (x, 0)")
    for n in [10000, 100000]:
        pts = [(i, 0) for i in range(n)]
        t, r = timeit(closest_pair, pts)
        print(f"n={n:<7} 距离={r.distance} 点对数={len(r.pairs)} 耗时={t:.4f}s")


if __name__ == "__main__":
    main()
