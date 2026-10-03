"""bipartite_matching 自测（python3 -m unittest，仅标准库）。

覆盖：空图、完全二分图、孤点、多重边、增广轮次数据、
合法性断言、带权匹配及其与不带权结果的关系，
并用小规模暴力枚举交叉验证最大基数与最大权重。
"""

import unittest

from bipartite_matching import (
    build_adjacency,
    max_matching,
    max_weight_matching,
    assert_valid_matching,
)


def brute_force_max_size(left, right, edge_set):
    """子集 DP 枚举求最大匹配基数（小规模交叉验证）。"""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def dp(i, used_mask):
        if i == len(left):
            return 0
        best = dp(i + 1, used_mask)  # 左节点 i 不匹配
        for j, v in enumerate(right):
            if not (used_mask >> j) & 1 and (left[i], v) in edge_set:
                best = max(best, 1 + dp(i + 1, used_mask | (1 << j)))
        return best

    return dp(0, 0)


def brute_force_max_weight(left, right, weights):
    """子集 DP 枚举求最大总权重（小规模交叉验证）。"""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def dp(i, used_mask):
        if i == len(left):
            return 0
        best = dp(i + 1, used_mask)  # 左节点 i 不匹配
        for j, v in enumerate(right):
            w = weights.get((left[i], v), 0)
            if not (used_mask >> j) & 1 and w > 0:
                best = max(best, w + dp(i + 1, used_mask | (1 << j)))
        return best

    return dp(0, 0)


class TestAdjacency(unittest.TestCase):
    def test_multi_edges_dedup(self):
        adj, edge_set = build_adjacency(["u1", "u2"], ["v1"],
                                        [("u1", "v1"), ("u1", "v1"), ("u2", "v1")])
        self.assertEqual(adj["u1"], ["v1"])          # 多重边去重
        self.assertEqual(adj["u2"], ["v1"])
        self.assertEqual(len(edge_set), 2)

    def test_isolated_node_kept(self):
        adj, _ = build_adjacency(["u1", "u2"], ["v1"], [("u1", "v1")])
        self.assertEqual(adj["u2"], [])              # 孤点有空邻接表

    def test_bad_endpoint_raises(self):
        with self.assertRaises(ValueError):
            build_adjacency(["u1"], ["v1"], [("uX", "v1")])
        with self.assertRaises(ValueError):
            build_adjacency(["u1"], ["v1"], [("u1", "vX")])


class TestMaxMatching(unittest.TestCase):
    def test_empty_graphs(self):
        # 两侧皆空
        matching, trace = max_matching([], [], [])
        self.assertEqual(matching, {})
        self.assertEqual(trace, [])
        # 左部全为孤点
        matching, trace = max_matching(["u1", "u2"], ["v1"], [])
        self.assertEqual(matching, {})
        self.assertTrue(all(not r["augmented"] for r in trace))
        # 右部为空
        matching, _ = max_matching(["u1"], [], [])
        self.assertEqual(matching, {})

    def test_complete_bipartite(self):
        for m, n in [(1, 1), (2, 3), (3, 2), (4, 4)]:
            left = [f"u{i}" for i in range(m)]
            right = [f"v{j}" for j in range(n)]
            edges = [(u, v) for u in left for v in right]
            matching, trace = max_matching(left, right, edges)
            self.assertEqual(len(matching), min(m, n))
            self._check_trace(trace, expected_final=min(m, n))
            assert_valid_matching(matching, set(edges), left, right)

    def test_isolated_nodes(self):
        left = ["u0", "u1", "u2", "u3"]
        right = ["v0", "v1", "iso"]
        edges = [("u0", "v0"), ("u1", "v0"), ("u1", "v1"),
                 ("u2", "v1"), ("iso_u" if False else "u0", "v1")]
        matching, trace = max_matching(left, right, edges)
        self.assertEqual(len(matching), 2)  # "iso" 与 "u3" 都是孤点
        matched_u = set(matching)
        self.assertNotIn("u3", matched_u)
        self.assertNotIn("iso", set(matching.values()))
        self._check_trace(trace, expected_final=2)

    def test_multi_edges_same_result(self):
        left, right = ["a", "b"], ["x", "y"]
        simple = [("a", "x"), ("b", "x"), ("b", "y")]
        multi = [e for e in simple for _ in range(3)]
        m1, _ = max_matching(left, right, simple)
        m2, _ = max_matching(left, right, multi)
        self.assertEqual(m1, m2)
        self.assertEqual(len(m2), 2)

    def test_trace_size_changes(self):
        # 教科书例子：u1-v1, u2-v1, u2-v2, u3-v2
        left, right = ["u1", "u2", "u3"], ["v1", "v2"]
        edges = [("u1", "v1"), ("u2", "v1"), ("u2", "v2"), ("u3", "v2")]
        matching, trace = max_matching(left, right, edges)
        self.assertEqual(len(matching), 2)
        # 每轮记录单调不减、增量恰为 0 或 1
        sizes = [(r["size_before"], r["augmented"], r["size_after"]) for r in trace]
        self.assertEqual(sizes, [(0, True, 1), (1, True, 2), (2, False, 2)])
        self._check_trace(trace, expected_final=2)

    def test_brute_force_random_small(self):
        import random
        rng = random.Random(42)
        for trial in range(60):
            m, n = rng.randint(0, 4), rng.randint(0, 4)
            left = [f"u{i}" for i in range(m)]
            right = [f"v{j}" for j in range(n)]
            edge_set = {(u, v) for u in left for v in right if rng.random() < 0.5}
            matching, trace = max_matching(left, right, list(edge_set))
            assert_valid_matching(matching, edge_set, left, right)
            self.assertEqual(len(matching), brute_force_max_size(left, right, edge_set))
            self._check_trace(trace, expected_final=len(matching))

    def _check_trace(self, trace, expected_final):
        prev = 0
        for r in trace:
            self.assertEqual(r["size_before"], prev)
            self.assertEqual(r["size_after"], r["size_before"] + int(r["augmented"]))
            self.assertIn(r["size_after"] - r["size_before"], (0, 1))
            prev = r["size_after"]
        self.assertEqual(prev, expected_final)


class TestValidityAssertion(unittest.TestCase):
    def setUp(self):
        self.edge_set = {("u1", "v1"), ("u2", "v2")}

    def test_valid(self):
        self.assertTrue(assert_valid_matching({"u1": "v1", "u2": "v2"}, self.edge_set))

    def test_duplicate_right_rejected(self):
        with self.assertRaises(AssertionError):
            assert_valid_matching({"u1": "v1", "u2": "v1"}, self.edge_set)

    def test_nonexistent_edge_rejected(self):
        with self.assertRaises(AssertionError):
            assert_valid_matching({"u1": "v2"}, self.edge_set)


class TestMaxWeightMatching(unittest.TestCase):
    def test_empty(self):
        matching, total = max_weight_matching([], [], [])
        self.assertEqual((matching, total), ({}, 0))
        matching, total = max_weight_matching(["u1"], ["v1"], [])
        self.assertEqual((matching, total), ({}, 0))

    def test_known_optimum(self):
        #   v1  v2  v3
        # u1  3   1   2
        # u2  1   2   4
        # u3  2   5   1   最优：u1-v1(3)+u2-v3(4)+u3-v2(5)=12
        left, right = ["u1", "u2", "u3"], ["v1", "v2", "v3"]
        wedges = [("u1", "v1", 3), ("u1", "v2", 1), ("u1", "v3", 2),
                  ("u2", "v1", 1), ("u2", "v2", 2), ("u2", "v3", 4),
                  ("u3", "v1", 2), ("u3", "v2", 5), ("u3", "v3", 1)]
        matching, total = max_weight_matching(left, right, wedges)
        self.assertEqual(total, 12)
        self.assertEqual(matching, {"u1": "v1", "u2": "v3", "u3": "v2"})

    def test_rectangular_and_isolated(self):
        left, right = ["u1", "u2", "u3"], ["v1", "v2"]
        wedges = [("u1", "v1", 10), ("u2", "v2", 9)]  # u3 孤点
        matching, total = max_weight_matching(left, right, wedges)
        self.assertEqual(total, 19)
        self.assertEqual(matching, {"u1": "v1", "u2": "v2"})

    def test_zero_weight_edge_skipped(self):
        # 权为 0 的边不应匹配（不匹配同样得 0，但避免占用节点）
        matching, total = max_weight_matching(["u1"], ["v1"], [("u1", "v1", 0)])
        self.assertEqual((matching, total), ({}, 0))

    def test_multi_weighted_edges_max_kept(self):
        matching, total = max_weight_matching(
            ["u1"], ["v1"], [("u1", "v1", 3), ("u1", "v1", 7), ("u1", "v1", 2)])
        self.assertEqual((matching, total), ({"u1": "v1"}, 7))

    def test_uniform_weight_equals_max_cardinality(self):
        # 关系之一：所有边权相同时，最大权匹配退化为最大基数匹配
        left, right = ["u1", "u2", "u3"], ["v1", "v2"]
        edges = [("u1", "v1"), ("u2", "v1"), ("u2", "v2"), ("u3", "v2")]
        m_card, _ = max_matching(left, right, edges)
        m_weight, total = max_weight_matching(left, right, [(u, v, 1) for u, v in edges])
        self.assertEqual(total, len(m_card))
        self.assertEqual(len(m_weight), len(m_card))

    def test_weighted_relation_to_unweighted(self):
        # 关系之二：带权最优的总权重，不低于不带权最大匹配在同一权重下的总权重；
        # 且带权匹配基数不超过不带权最大匹配基数。
        import random
        rng = random.Random(7)
        for _ in range(60):
            m, n = rng.randint(1, 4), rng.randint(1, 4)
            left = [f"u{i}" for i in range(m)]
            right = [f"v{j}" for j in range(n)]
            edges = [(u, v) for u in left for v in right if rng.random() < 0.6]
            weights = {(u, v): rng.randint(1, 9) for u, v in edges}
            wedges = [(u, v, w) for (u, v), w in weights.items()]

            m_card, _ = max_matching(left, right, edges)
            m_opt, total_opt = max_weight_matching(left, right, wedges)

            self.assertLessEqual(len(m_opt), len(m_card))  # 基数不会更大
            card_weight = sum(weights[e] for e in m_card.items())
            self.assertGreaterEqual(total_opt, card_weight)
            self.assertEqual(total_opt,
                             brute_force_max_weight(left, right, weights))
            assert_valid_matching(m_opt, set(edges), left, right)


if __name__ == "__main__":
    unittest.main(verbosity=2)
