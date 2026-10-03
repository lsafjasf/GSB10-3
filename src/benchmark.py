"""Sparse vs dense timing: exact APSP-BFS vs k-sweep estimate.

Run: python3 src/benchmark.py
"""

import random
import time

from diameter import estimate_diameter, exact_diameter


def random_connected_graph(n, m, rng):
    adj = {i: set() for i in range(n)}
    for v in range(1, n):                      # spanning tree -> connected
        u = rng.randrange(v)
        adj[u].add(v)
        adj[v].add(u)
    edges = n - 1
    while edges < m:
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v and v not in adj[u]:
            adj[u].add(v)
            adj[v].add(u)
            edges += 1
    return adj


def timed(fn, adj, **kw):
    t0 = time.perf_counter()
    stats = fn(adj, **kw)
    return stats, time.perf_counter() - t0


def main():
    rng = random.Random(2026)
    cases = [
        ("sparse  n=2000  m~2n   ", 2000, 4000),
        ("sparse  n=4000  m~2n   ", 4000, 8000),
        ("dense   n=1000  m~n^2/8", 1000, 125000),
        ("dense   n=2000  m~n^2/8", 2000, 500000),
    ]
    header = (f"{'graph':<26}{'exact APSP-BFS':>16}{'k-sweep est.':>16}"
              f"{'speedup':>10}   diam(exact/est)  rad(exact/est)")
    print(header)
    print("-" * len(header))
    for name, n, m in cases:
        adj = random_connected_graph(n, m, rng)
        exact, t_exact = timed(exact_diameter, adj)
        est, t_est = timed(estimate_diameter, adj, sweeps=2,
                           rng=random.Random(0))
        print(f"{name:<26}{t_exact*1e3:>13.1f} ms{t_est*1e3:>13.1f} ms"
              f"{t_exact/t_est:>9.0f}x   "
              f"{exact.diameter:>4}/{est.diameter:<4}        "
              f"{exact.radius:>4}/{est.radius:<4}")


if __name__ == "__main__":
    main()
