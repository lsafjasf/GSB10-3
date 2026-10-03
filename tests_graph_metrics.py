"""graph_metrics 自测：边界用例 + 关系断言 + 随机交叉验证。

运行：python3 tests_graph_metrics.py
"""

import random
import sys
import unittest

sys.path.insert(0, ".")
from graph_metrics import (
    MetricError,
    assert_metric_relations,
    approximate_diameter_radius_center,
    diameter_radius_center,
    eccentricity_bounds,
    exact_eccentricities,
    tree_diameter_radius_center,
)


def path_graph(n):
    adj = [[] for _ in range(n)]
    for i in range(n - 1):
        adj[i].append(i + 1)
        adj[i + 1].append(i)
    return adj


def cycle_graph(n):
    adj = [[] for _ in range(n)]
    for i in range(n):
        adj[i].append((i + 1) % n)
        adj[(i + 1) % n].append(i)
    return adj


def random_connected_graph(n, extra_edges, rng):
    adj = [set() for _ in range(n)]
    for v in range(1, n):  # 随机树保证连通
        u = rng.randrange(v)
        adj[v].add(u)
        adj[u].add(v)
    for _ in range(extra_edges):
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v:
            adj[u].add(v)
            adj[v].add(u)
    return [sorted(s) for s in adj]


def random_tree(n, rng):
    return random_connected_graph(n, 0, rng)


class TestEdgeCases(unittest.TestCase):
    def test_single_vertex(self):
        result = diameter_radius_center([[]])
        self.assertTrue(result["connected"])
        self.assertEqual(result["diameter"], 0)
        self.assertEqual(result["radius"], 0)
        self.assertEqual(result["center"], [0])
        self.assertTrue(assert_metric_relations(result))

    def test_two_vertices(self):
        result = diameter_radius_center([[1], [0]])
        self.assertEqual(result["diameter"], 1)
        self.assertEqual(result["radius"], 1)
        self.assertEqual(result["center"], [0, 1])
        self.assertTrue(assert_metric_relations(result))

    def test_path_graphs(self):
        # 链 P5: 0-1-2-3-4，直径 4，半径 2，中心 {2}
        result = diameter_radius_center(path_graph(5))
        self.assertEqual(result["diameter"], 4)
        self.assertEqual(result["radius"], 2)
        self.assertEqual(result["center"], [2])
        self.assertTrue(assert_metric_relations(result))
        # 链 P6: 直径 5，半径 3，中心 {2, 3}
        result = diameter_radius_center(path_graph(6))
        self.assertEqual(result["diameter"], 5)
        self.assertEqual(result["radius"], 3)
        self.assertEqual(result["center"], [2, 3])
        self.assertTrue(assert_metric_relations(result))

    def test_disconnected(self):
        # 两个分量：P3 与单点
        adj = [[1], [0, 2], [1], []]
        result = diameter_radius_center(adj)
        self.assertFalse(result["connected"])
        self.assertEqual(result["diameter"], float("inf"))
        self.assertIsNone(result["radius"])
        self.assertEqual(result["center"], [])
        self.assertEqual(len(result["components"]), 2)
        comp0 = result["components"][0]
        self.assertEqual(comp0["diameter"], 2)
        self.assertEqual(comp0["radius"], 1)
        self.assertEqual(comp0["center"], [1])
        comp1 = result["components"][1]
        self.assertEqual(comp1["diameter"], 0)
        self.assertEqual(comp1["center"], [3])
        ecc, _, _ = exact_eccentricities(adj)
        self.assertEqual(ecc, [2, 1, 2, 0])  # 分量内偏心率
        self.assertTrue(assert_metric_relations(result))

    def test_cycle(self):
        # C5：直径=半径=2，所有顶点都是中心（一般图中心可超过 2 个）
        result = diameter_radius_center(cycle_graph(5))
        self.assertEqual(result["diameter"], 2)
        self.assertEqual(result["radius"], 2)
        self.assertEqual(result["center"], [0, 1, 2, 3, 4])
        self.assertTrue(assert_metric_relations(result))

    def test_invalid_input(self):
        with self.assertRaises(MetricError):
            diameter_radius_center([[2], [0]])          # 越界
        with self.assertRaises(MetricError):
            diameter_radius_center([[1], []])           # 缺反向边
        with self.assertRaises(MetricError):
            eccentricity_bounds([[1], [0]], [])         # 空地标集


class TestBounds(unittest.TestCase):
    def test_bounds_are_valid_and_certify(self):
        # 链 P5，地标 {0, 2, 4}：3 次 BFS 即可认证全部 5 个顶点
        adj = path_graph(5)
        bounds = eccentricity_bounds(adj, [0, 2, 4])
        ecc, _, _ = exact_eccentricities(adj)
        for v in range(5):
            self.assertLessEqual(bounds["lower"][v], ecc[v])
            self.assertLessEqual(ecc[v], bounds["upper"][v])
            self.assertTrue(bounds["exact_flags"][v])
        self.assertEqual(bounds["landmark_ecc"], {0: 4, 2: 2, 4: 4})

    def test_upper_bound_only_case(self):
        # 链 P7，地标 {0}：只能给出上界与（平凡的）下界
        adj = path_graph(7)
        bounds = eccentricity_bounds(adj, [0])
        ecc, _, _ = exact_eccentricities(adj)
        for v in range(7):
            self.assertLessEqual(bounds["lower"][v], ecc[v])
            self.assertLessEqual(ecc[v], bounds["upper"][v])
        self.assertFalse(any(bounds["exact_flags"][v] for v in range(1, 7)))

    def test_approximate_intervals_contain_truth(self):
        rng = random.Random(7)
        for trial in range(30):
            n = rng.randrange(2, 40)
            adj = random_connected_graph(n, rng.randrange(0, n), rng)
            k = rng.randrange(1, max(2, n // 3))
            landmarks = rng.sample(range(n), k)
            approx = approximate_diameter_radius_center(adj, landmarks)
            exact = diameter_radius_center(adj)
            comp = approx["components"][0]
            self.assertLessEqual(comp["diameter_low"], exact["diameter"])
            self.assertLessEqual(exact["diameter"], comp["diameter_up"])
            self.assertLessEqual(comp["radius_low"], exact["radius"])
            self.assertLessEqual(exact["radius"], comp["radius_up"])
            for v in comp["certified_center"]:
                self.assertIn(v, exact["center"])
            for v in exact["center"]:
                self.assertIn(v, comp["possible_center"])
            if comp["all_certified"]:
                self.assertEqual(comp["diameter_low"], exact["diameter"])
                self.assertEqual(comp["diameter_up"], exact["diameter"])
                self.assertEqual(comp["radius_low"], exact["radius"])
                self.assertEqual(comp["radius_up"], exact["radius"])

    def test_all_vertices_as_landmarks_is_exact(self):
        adj = random_connected_graph(15, 10, random.Random(3))
        approx = approximate_diameter_radius_center(adj, list(range(15)))
        exact = diameter_radius_center(adj)
        comp = approx["components"][0]
        self.assertTrue(comp["all_certified"])
        self.assertEqual(comp["diameter_low"], exact["diameter"])
        self.assertEqual(comp["radius_up"], exact["radius"])
        self.assertEqual(comp["certified_center"], exact["center"])


class TestTreeDoubleBFS(unittest.TestCase):
    def test_tree_matches_exact(self):
        rng = random.Random(11)
        for _ in range(30):
            n = rng.randrange(1, 60)
            adj = random_tree(n, rng)
            fast = tree_diameter_radius_center(adj)
            exact = diameter_radius_center(adj)
            self.assertEqual(fast["diameter"], exact["diameter"])
            self.assertEqual(fast["radius"], exact["radius"])
            self.assertEqual(fast["center"], exact["center"])
            self.assertLessEqual(len(fast["center"]), 2)

    def test_tree_single_and_two_vertices(self):
        self.assertEqual(tree_diameter_radius_center([[]]),
                         {"diameter": 0, "radius": 0, "center": [0]})
        self.assertEqual(tree_diameter_radius_center([[1], [0]]),
                         {"diameter": 1, "radius": 1, "center": [0, 1]})


class TestRandomCrossCheck(unittest.TestCase):
    def test_relations_hold_on_random_graphs(self):
        rng = random.Random(42)
        for _ in range(50):
            n = rng.randrange(1, 50)
            adj = random_connected_graph(n, rng.randrange(0, 2 * n), rng)
            result = diameter_radius_center(adj)
            self.assertTrue(assert_metric_relations(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
