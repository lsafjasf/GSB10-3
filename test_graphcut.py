"""test_graphcut.py — graphcut 库的自测（unittest，仅标准库）。

运行:  python3 test_graphcut.py -v
"""

import random
import unittest

from graphcut import (
    assert_cut_valid,
    balanced_min_cut,
    balanced_min_cut_exact,
    balanced_min_cut_flow,
    crossing_edges,
    global_min_cut,
    min_s_t_cut,
    reachable_without,
)


def clique(nodes):
    nodes = list(nodes)
    return [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1:]]


def two_cliques_bridge(a_nodes, b_nodes, bridges):
    return clique(a_nodes) + clique(b_nodes) + list(bridges)


class TestMinCut(unittest.TestCase):
    """基础最大流 / 最小割。"""

    def test_path_graph(self):
        # 0-1-2-3：断开任意一条边即可分隔 0 和 3
        value, left, right, ce = min_s_t_cut(4, [(0, 1), (1, 2), (2, 3)], 0, 3)
        self.assertEqual(value, 1)
        self.assertEqual(len(ce), 1)
        assert_cut_valid(4, [(0, 1), (1, 2), (2, 3)], left, right, ce)

    def test_parallel_disjoint_paths(self):
        # 0 到 3 有两条点不相交路径 -> 最小割 = 2
        edges = [(0, 1), (1, 3), (0, 2), (2, 3)]
        value, left, right, ce = min_s_t_cut(4, edges, 0, 3)
        self.assertEqual(value, 2)
        assert_cut_valid(4, edges, left, right, ce)

    def test_complete_graph_k5(self):
        # K5 中分隔任意两点需切断 4 条边
        edges = clique(range(5))
        value, left, right, ce = min_s_t_cut(5, edges, 0, 4)
        self.assertEqual(value, 4)
        assert_cut_valid(5, edges, left, right, ce)

    def test_single_node(self):
        # 单点图：没有边，割为 0，一侧为空
        size, left, right, ce = balanced_min_cut(1, [], tol=0)
        self.assertEqual((size, len(left), len(right), ce), (0, 1, 0, []))
        assert_cut_valid(1, [], left, right, ce)

    def test_disconnected_graph_no_cut_edges(self):
        # 两个不连通的三角形：不需要删任何边，割 = 0
        edges = clique([0, 1, 2]) + clique([3, 4, 5])
        size, left, right, ce = global_min_cut(6, edges)
        self.assertEqual(size, 0)
        self.assertEqual(ce, [])
        # 删掉 0 条割边后，两侧本来就连不通
        assert_cut_valid(6, edges, left, right, ce)

    def test_max_flow_min_cut_theorem(self):
        # 随机图上验证：割边数 == 最大流值，且删边后两侧不连通
        rng = random.Random(7)
        for _ in range(20):
            n = rng.randint(2, 9)
            edges = {(u, v) for u in range(n) for v in range(u + 1, n)
                     if rng.random() < 0.4}
            value, left, right, ce = min_s_t_cut(n, edges, 0, n - 1)
            self.assertEqual(len(ce), value)
            assert_cut_valid(n, edges, left, right, ce)


class TestConnectivityAssertion(unittest.TestCase):
    """连通性断言本身也要被测试：合法割通过，非法割报错。"""

    def test_valid_cut_passes(self):
        edges = [(0, 1), (1, 2), (2, 3)]
        assert_cut_valid(4, edges, {0, 1}, {2, 3}, [(1, 2)])

    def test_wrong_cut_edges_rejected(self):
        edges = [(0, 1), (1, 2), (2, 3), (0, 3)]  # 环
        # 只删 (1,2) 不够：0-3 还连着
        with self.assertRaises(AssertionError):
            assert_cut_valid(4, edges, {0, 1}, {2, 3}, [(1, 2)])

    def test_reachable_without(self):
        edges = [(0, 1), (1, 2), (2, 3)]
        self.assertEqual(reachable_without(4, edges, [(1, 2)]), {0, 1})
        self.assertEqual(reachable_without(4, edges, []), {0, 1, 2, 3})


class TestBalancedCut(unittest.TestCase):
    """平衡约束对结果的影响。"""

    def test_complete_graph_k6(self):
        # K6 任意 k|(6-k) 的割有 k*(6-k) 条跨边：
        #   tol=0 (3|3) -> 9；tol=2 (2|4) -> 8；tol=4 (1|5) -> 5
        edges = clique(range(6))
        for tol, expect in [(0, 9), (2, 8), (4, 5)]:
            size, left, right, ce = balanced_min_cut(6, edges, tol)
            self.assertEqual(size, expect, f"tol={tol}")
            self.assertLessEqual(abs(len(left) - len(right)), tol)
            assert_cut_valid(6, edges, left, right, ce)

    def test_complete_graph_k5(self):
        # K5: tol=1 (2|3) -> 6；tol=3 (1|4) -> 4
        edges = clique(range(5))
        for tol, expect in [(1, 6), (3, 4)]:
            size, left, right, ce = balanced_min_cut(5, edges, tol)
            self.assertEqual(size, expect, f"tol={tol}")
            assert_cut_valid(5, edges, left, right, ce)

    def test_two_unequal_cliques_bridge(self):
        # K8 与 K4 由一座桥相连：
        #   不平衡 (tol>=4)：割桥即可，割=1，两侧 8|4
        #   平衡 (tol=0, 6|6)：从 K8 挖 2 个点（含桥端点）到 K4 侧，
        #   桥变为内部边，最优割 = 2*6 = 12 条团内边
        edges = two_cliques_bridge(range(8), range(8, 12), [(7, 8)])
        size_loose, left, right, ce = balanced_min_cut(12, edges, tol=4)
        self.assertEqual(size_loose, 1)
        self.assertEqual(sorted(map(len, (left, right))), [4, 8])
        assert_cut_valid(12, edges, left, right, ce)

        size_strict, left, right, ce = balanced_min_cut(12, edges, tol=0)
        self.assertEqual(sorted(map(len, (left, right))), [6, 6])
        self.assertEqual(size_strict, 12)
        self.assertGreater(size_strict, size_loose,
                           "平衡约束收紧时割只能变大或不变")
        assert_cut_valid(12, edges, left, right, ce)

    def test_balance_monotonicity(self):
        # 同一图上 tol 越小，割大小单调不减
        edges = two_cliques_bridge(range(6), range(6, 10), [(5, 6), (4, 7)])
        sizes = [balanced_min_cut(10, edges, tol)[0] for tol in (0, 2, 4, 6, 8)]
        for a, b in zip(sizes, sizes[1:]):
            self.assertGreaterEqual(a, b)

    def test_flow_heuristic_vs_exact_on_random_graphs(self):
        # 流模型参数化启发式 vs 精确枚举（小随机图交叉验证）：
        # 启发式给出的是可行割，因此割大小必然 >= 精确最优；
        # 在多数随机实例上两者应当一致。
        rng = random.Random(42)
        checked = matched = 0
        for _ in range(30):
            n = rng.randint(2, 10)
            edges = sorted({(u, v) for u in range(n) for v in range(u + 1, n)
                            if rng.random() < 0.45})
            for tol in (0, 1, 2, n):
                try:
                    exact = balanced_min_cut_exact(n, edges, tol)
                except ValueError:
                    continue
                got = balanced_min_cut_flow(n, edges, tol)
                self.assertGreaterEqual(got[0], exact[0],
                                        f"n={n} tol={tol} edges={edges}")
                self.assertLessEqual(abs(len(got[1]) - len(got[2])), tol)
                assert_cut_valid(n, edges, got[1], got[2], got[3])
                checked += 1
                matched += (got[0] == exact[0])
        self.assertGreater(checked, 50)
        self.assertGreaterEqual(matched / checked, 0.8,
                                f"启发式命中率过低: {matched}/{checked}")

    def test_flow_heuristic_exact_on_structured_graphs(self):
        # 流方法在典型结构图上应达到精确最优
        edges = clique(range(6))
        for tol, expect in [(0, 9), (2, 8), (4, 5)]:
            self.assertEqual(balanced_min_cut_flow(6, edges, tol)[0], expect)
        edges = two_cliques_bridge(range(6), range(6, 10), [(5, 6), (4, 7)])
        for tol in (0, 2, 4, 6, 8):
            self.assertEqual(balanced_min_cut_flow(10, edges, tol)[0],
                             balanced_min_cut_exact(10, edges, tol)[0])

    def test_empty_graph(self):
        # 无边图：任意分组割都是 0
        size, left, right, ce = balanced_min_cut(4, [], tol=0)
        self.assertEqual(size, 0)
        self.assertEqual(ce, [])
        assert_cut_valid(4, [], left, right, ce)


if __name__ == "__main__":
    unittest.main()
