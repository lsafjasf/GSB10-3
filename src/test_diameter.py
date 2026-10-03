"""Self tests for src/diameter.py.  Run: python3 src/test_diameter.py"""

import math
import random
import unittest

from diameter import (assert_relationships, estimate_diameter, exact_diameter,
                      normalize_adj)


def path_graph(n):
    return {i: ([i - 1] if i else []) + ([i + 1] if i < n - 1 else [])
            for i in range(n)}


def cycle_graph(n):
    return {i: [(i - 1) % n, (i + 1) % n] for i in range(n)}


def random_tree(n, rng):
    adj = {i: [] for i in range(n)}
    for v in range(1, n):
        u = rng.randrange(v)
        adj[u].append(v)
        adj[v].append(u)
    return adj


def random_graph(n, m, rng):
    adj = {i: set() for i in range(n)}
    edges = set()
    while len(edges) < m:
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v and (u, v) not in edges and (v, u) not in edges:
            edges.add((u, v))
            adj[u].add(v)
            adj[v].add(u)
    return adj


class EdgeCases(unittest.TestCase):
    def test_single_node(self):
        stats = exact_diameter({0: []})
        self.assertTrue(stats.connected)
        self.assertEqual(stats.diameter, 0)
        self.assertEqual(stats.radius, 0)
        self.assertEqual(stats.center, frozenset({0}))
        assert_relationships(stats, {0: []}, exact_check=True)
        est = estimate_diameter({0: []})
        self.assertEqual((est.diameter, est.radius), (0, 0))

    def test_two_nodes(self):
        adj = {0: [1], 1: [0]}
        stats = exact_diameter(adj)
        self.assertEqual(stats.diameter, 1)
        self.assertEqual(stats.radius, 1)
        self.assertEqual(stats.center, frozenset({0, 1}))
        assert_relationships(stats, adj, exact_check=True)
        est = estimate_diameter(adj)
        self.assertEqual((est.diameter, est.radius), (1, 1))

    def test_path_odd(self):          # 0-1-2-3-4
        adj = path_graph(5)
        stats = exact_diameter(adj)
        self.assertEqual(stats.diameter, 4)
        self.assertEqual(stats.radius, 2)
        self.assertEqual(stats.center, frozenset({2}))
        assert_relationships(stats, adj, exact_check=True)
        est = estimate_diameter(adj)
        self.assertEqual((est.diameter, est.radius), (4, 2))
        self.assertEqual(est.center, frozenset({2}))

    def test_path_even(self):         # 0-1-2-3
        adj = path_graph(4)
        stats = exact_diameter(adj)
        self.assertEqual(stats.diameter, 3)
        self.assertEqual(stats.radius, 2)
        self.assertEqual(stats.center, frozenset({1, 2}))
        assert_relationships(stats, adj, exact_check=True)
        est = estimate_diameter(adj)
        self.assertEqual((est.diameter, est.radius), (3, 2))

    def test_disconnected(self):
        adj = {0: [1], 1: [0], 2: [3], 3: [2, 4], 4: [3]}
        stats = exact_diameter(adj)
        self.assertFalse(stats.connected)
        self.assertIs(stats.diameter, math.inf)
        self.assertIs(stats.radius, math.inf)
        self.assertEqual(stats.center, frozenset())
        self.assertEqual(len(stats.components), 2)
        self.assertEqual(stats.components[0].diameter, 2)   # path 2-3-4
        self.assertEqual(stats.components[1].diameter, 1)   # edge 0-1
        assert_relationships(stats, adj, exact_check=True)
        est = estimate_diameter(adj)
        self.assertFalse(est.connected)
        assert_relationships(est, adj)

    def test_cycle(self):
        adj = cycle_graph(6)
        stats = exact_diameter(adj)
        self.assertEqual((stats.diameter, stats.radius), (3, 3))
        self.assertEqual(stats.center, frozenset(range(6)))
        assert_relationships(stats, adj, exact_check=True)


class Exactness(unittest.TestCase):
    def test_estimate_exact_on_random_trees(self):
        rng = random.Random(42)
        for _ in range(30):
            adj = random_tree(rng.randrange(2, 60), rng)
            exact = exact_diameter(adj)
            est = estimate_diameter(adj, rng=random.Random(7))
            self.assertEqual(est.diameter, exact.diameter)
            self.assertEqual(est.radius, exact.radius)
            self.assertTrue(est.center <= exact.center)
            assert_relationships(est, adj)

    def test_estimate_bounds_on_random_graphs(self):
        rng = random.Random(1)
        for _ in range(30):
            n = rng.randrange(10, 50)
            adj = random_graph(n, rng.randrange(n - 1, 3 * n), rng)
            exact = exact_diameter(adj)
            est = estimate_diameter(adj, rng=random.Random(3))
            if not exact.connected:
                self.assertFalse(est.connected)
                continue
            # diameter estimate is a lower bound, radius an upper bound
            self.assertLessEqual(est.diameter, exact.diameter)
            self.assertGreaterEqual(est.radius, exact.radius)
            self.assertLessEqual(exact.diameter, 2 * est.radius)
            assert_relationships(est, adj)
            assert_relationships(exact, adj, exact_check=True)

    def test_normalize_isolated_node(self):
        adj = normalize_adj({0: [1]})
        self.assertIn(1, adj)
        stats = exact_diameter({0: [1]})
        self.assertEqual(stats.diameter, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
