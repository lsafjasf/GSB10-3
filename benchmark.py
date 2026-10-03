"""基准测试：预处理耗时、单次查询耗时、与朴素实现对比。

运行：python3 benchmark.py
"""

import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lca import LCA
from tests.reference import NaiveLCA


def random_tree(n, rng):
    return [(rng.randrange(v), v, rng.randint(1, 10**6)) for v in range(1, n)]


def chain_tree(n):
    return [(i - 1, i, i) for i in range(1, n)]


def star_tree(n):
    return [(0, v, v) for v in range(1, n)]


def bench(build_edges, label, n, q, rng):
    edges = build_edges(n)
    pairs = [(rng.randrange(n), rng.randrange(n)) for _ in range(q)]

    t0 = time.perf_counter()
    fast = LCA(n, edges)
    t_pre = time.perf_counter() - t0

    t0 = time.perf_counter()
    for u, v in pairs:
        fast.lca(u, v)
    t_lca = time.perf_counter() - t0

    t0 = time.perf_counter()
    for u, v in pairs:
        fast.query(u, v)
    t_query = time.perf_counter() - t0

    print(f"[{label}] n={n:,}  查询数={q:,}")
    print(f"  预处理:        {t_pre*1e3:9.2f} ms  ({t_pre/n*1e6:7.2f} us/节点)")
    print(f"  lca 单次:      {t_lca/q*1e6:9.2f} us  (总计 {t_lca*1e3:.2f} ms)")
    print(f"  query 单次:    {t_query/q*1e6:9.2f} us  (总计 {t_query*1e3:.2f} ms, 一次返回 lca+距离+路径最值)")
    return edges, pairs


def bench_naive(n, edges, pairs):
    naive = NaiveLCA(n, edges)
    t0 = time.perf_counter()
    for u, v in pairs:
        naive.lca(u, v)
    t = time.perf_counter() - t0
    print(f"  朴素 lca 单次: {t/len(pairs)*1e6:9.2f} us  (O(h) 逐层上溯，供对比)")


def bench_add_leaf():
    n0, add = 1000, 100_000
    rng = random.Random(1)
    tree = LCA(n0, random_tree(n0, rng))
    t0 = time.perf_counter()
    for _ in range(add):
        tree.add_leaf(rng.randrange(len(tree)), rng.randint(1, 100))
    t = time.perf_counter() - t0
    print(f"[add_leaf] 初始 n={n0:,}，增量插入 {add:,} 个叶子")
    print(f"  总耗时 {t*1e3:.2f} ms，均摊 {t/add*1e6:.2f} us/次（含倍增表扩容摊还）")


def main():
    rng = random.Random(20241004)
    q = 100_000

    print("=" * 64)
    print(f"Python {sys.version.split()[0]}  倍增法 LCA 基准")
    print("=" * 64)

    for n in (1_000, 10_000, 100_000):
        bench(lambda n: random_tree(n, rng), "随机树", n, q, rng)
        print()

    # 深链 + 朴素对比（朴素 O(h) 在深链上退化明显）
    n = 20_000
    edges, pairs = bench(chain_tree, "链状树", n, q, rng)
    bench_naive(n, edges, pairs[:20_000])
    print()

    n = 100_000
    bench(star_tree, "星形树", n, q, rng)
    print()

    bench_add_leaf()


if __name__ == "__main__":
    main()
