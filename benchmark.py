"""稀疏图 vs 稠密图耗时对比。

运行：python3 benchmark.py
精确模式 = 全顶点 BFS（O(V(V+E))）；近似模式 = 16 个随机地标点 BFS。
"""

import random
import sys
import time

sys.path.insert(0, ".")
from graph_metrics import (
    approximate_diameter_radius_center,
    assert_metric_relations,
    diameter_radius_center,
)


def random_connected_graph(n, target_edges, rng):
    adj = [set() for _ in range(n)]
    for v in range(1, n):
        u = rng.randrange(v)
        adj[v].add(u)
        adj[u].add(v)
    edges = n - 1
    while edges < target_edges:
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v and v not in adj[u]:
            adj[u].add(v)
            adj[v].add(u)
            edges += 1
    return [sorted(s) for s in adj]


def main():
    rng = random.Random(20261004)
    cases = [
        ("稀疏 (m≈2n)", 1000, 2000),
        ("稀疏 (m≈2n)", 2000, 4000),
        ("稀疏 (m≈2n)", 4000, 8000),
        ("稀疏 (m≈2n)", 8000, 16000),
        ("稠密 (m≈n^2/10)", 300, 9000),
        ("稠密 (m≈n^2/10)", 600, 36000),
        ("稠密 (m≈n^2/10)", 1000, 100000),
    ]
    print(f"{'类型':<18}{'n':>6}{'m':>10}{'精确(s)':>12}{'近似16地标(s)':>16}{'直径':>6}{'半径':>6}")
    for label, n, m in cases:
        adj = random_connected_graph(n, m, rng)
        t0 = time.perf_counter()
        result = diameter_radius_center(adj)
        t_exact = time.perf_counter() - t0
        assert_metric_relations(result)

        landmarks = rng.sample(range(n), 16)
        t0 = time.perf_counter()
        approx = approximate_diameter_radius_center(adj, landmarks)
        t_approx = time.perf_counter() - t0
        comp = approx["components"][0]
        certified = "精确" if comp["all_certified"] else \
            f"[{comp['diameter_low']},{comp['diameter_up']}]"

        print(f"{label:<18}{n:>6}{m:>10}{t_exact:>12.3f}{t_approx:>16.3f}"
              f"{result['diameter']:>6}{result['radius']:>6}  近似直径={certified}")


if __name__ == "__main__":
    main()
