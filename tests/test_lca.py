"""自测 + 对拍：倍增法 vs 逐层上溯朴素实现。

覆盖：
- 单节点树、链状树、星形树、随机树（多轮、多种规模）
- 节点自身作为祖先（lca(u, u)、祖先-后代查询）
- 负数 / 浮点边权
- 增量 add_leaf 后继续对拍、rebuild 换根
- 非法输入
运行：python3 -m unittest tests.test_lca -v   （或 python3 tests/test_lca.py）
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lca import LCA  # noqa: E402
from tests.reference import NaiveLCA  # noqa: E402


def random_tree(n, rng, weight_gen=None):
    """随机带根树：节点 i 的父节点在 [0, i) 中随机。"""
    edges = []
    for v in range(1, n):
        u = rng.randrange(v)
        w = weight_gen() if weight_gen else rng.randint(-100, 1000)
        edges.append((u, v, w))
    return edges


def edge_map(edges):
    weights = {}
    for u, v, w in edges:
        weights[(min(u, v), max(u, v))] = w
    return weights


class TestLCA(unittest.TestCase):

    def assert_agree(self, fast, naive, pairs):
        """对一组节点对，断言两种实现的三项结果完全一致。"""
        for u, v in pairs:
            with self.subTest(u=u, v=v):
                self.assertEqual(fast.lca(u, v), naive.lca(u, v))
                self.assertEqual(fast.distance(u, v), naive.distance(u, v))
                self.assertEqual(fast.path_max(u, v), naive.path_max(u, v))
                t = fast.query(u, v)
                self.assertEqual(t, (naive.lca(u, v),
                                     naive.distance(u, v),
                                     naive.path_max(u, v)))

    # ---------------- 边界形态 ---------------- #

    def test_single_node(self):
        fast = LCA(1, [])
        self.assertEqual(fast.lca(0, 0), 0)
        self.assertEqual(fast.distance(0, 0), 0)
        self.assertIsNone(fast.path_max(0, 0))
        self.assertEqual(fast.query(0, 0), (0, 0, None))
        self.assertEqual(fast.depth(0), 0)
        self.assertEqual(len(fast), 1)

    def test_chain(self):
        n = 50
        edges = [(i - 1, i, i * 10) for i in range(1, n)]  # 最大边权 490
        fast = LCA(n, edges)
        naive = NaiveLCA(n, edges)
        pairs = [(0, n - 1), (10, 40), (25, 25), (0, 0), (33, 7), (49, 0)]
        self.assert_agree(fast, naive, pairs)
        self.assertEqual(fast.lca(0, n - 1), 0)
        self.assertEqual(fast.distance(0, n - 1), n - 1)
        self.assertEqual(fast.path_max(10, 40), 400)
        self.assertEqual(fast.path_max(49, 0), 490)

    def test_star(self):
        n = 100
        edges = [(0, v, -v) for v in range(1, n)]  # 负边权
        fast = LCA(n, edges)
        naive = NaiveLCA(n, edges)
        rng = random.Random(7)
        pairs = [(0, 0), (1, 1), (1, 99), (50, 75), (0, rng.randrange(1, n))]
        self.assert_agree(fast, naive, pairs)
        self.assertEqual(fast.lca(1, 99), 0)
        self.assertEqual(fast.distance(1, 99), 2)
        self.assertEqual(fast.path_max(1, 99), -1)

    def test_self_as_ancestor(self):
        # lca(u, u) == u；祖先与后代的 lca 就是祖先
        n = 30
        edges = [(i // 2, i, i) for i in range(1, n)]
        fast = LCA(n, edges)
        for u in range(n):
            self.assertEqual(fast.lca(u, u), u)
            self.assertEqual(fast.distance(u, u), 0)
            self.assertIsNone(fast.path_max(u, u))
        self.assertEqual(fast.lca(0, 29), 0)
        self.assertEqual(fast.lca(3, 13), 3)   # 3 是 13 的祖先
        self.assertEqual(fast.lca(13, 3), 3)
        self.assertEqual(fast.distance(7, 28), 2)  # 28 -> 14 -> 7

    def test_float_weights(self):
        n = 20
        rng = random.Random(11)
        edges = random_tree(n, rng, weight_gen=lambda: round(rng.uniform(-1, 1), 4))
        fast = LCA(n, edges)
        naive = NaiveLCA(n, edges)
        pairs = [(u, v) for u in range(n) for v in range(u, n)]
        self.assert_agree(fast, naive, pairs)

    # ---------------- 随机对拍 ---------------- #

    def test_random_trees(self):
        rng = random.Random(20241004)
        for n in (2, 3, 7, 31, 100, 500):
            edges = random_tree(n, rng)
            fast = LCA(n, edges)
            naive = NaiveLCA(n, edges)
            pairs = []
            for _ in range(min(2000, n * n)):
                pairs.append((rng.randrange(n), rng.randrange(n)))
            pairs += [(u, u) for u in range(n)]
            self.assert_agree(fast, naive, pairs)

    def test_deep_chain_random_pairs(self):
        n = 2000
        edges = [(i - 1, i, rng_weight(i)) for i in range(1, n)]
        rng = random.Random(5)
        fast = LCA(n, edges)
        naive = NaiveLCA(n, edges)
        pairs = [(rng.randrange(n), rng.randrange(n)) for _ in range(500)]
        self.assert_agree(fast, naive, pairs)
        # 深链上朴素实现 O(h) 很慢，这里顺便验证查询结果与距离
        self.assertEqual(fast.distance(0, n - 1), n - 1)

    # ---------------- 动态增量 ---------------- #

    def test_add_leaf_incremental(self):
        rng = random.Random(42)
        n = 40
        edges = random_tree(n, rng)
        fast = LCA(n, edges)
        naive = NaiveLCA(n, edges)

        added = []
        for k in range(60):
            parent = rng.randrange(len(fast))
            w = rng.randint(-50, 50)
            vf = fast.add_leaf(parent, w)
            vn = naive.add_leaf(parent, w)
            self.assertEqual(vf, vn)
            added.append(vf)

        pairs = []
        total = len(fast)
        for _ in range(3000):
            pairs.append((rng.randrange(total), rng.randrange(total)))
        pairs += [(v, v) for v in added]
        self.assert_agree(fast, naive, pairs)

    def test_add_leaf_chain_extension_triggers_expand(self):
        # 从 2 个节点起步，持续往链尾挂叶子，强制倍增表多次扩容
        fast = LCA(2, [(0, 1, 1)])
        naive = NaiveLCA(2, [(0, 1, 1)])
        rng = random.Random(3)
        last = 1
        for k in range(2, 200):
            w = rng.randint(-10, 10)
            last = fast.add_leaf(last, w)
            naive.add_leaf(last - 1, w)
        pairs = [(0, 199), (1, 198), (100, 100), (50, 150), (0, 0)]
        self.assert_agree(fast, naive, pairs)
        self.assertEqual(fast.depth(199), 199)
        self.assertEqual(fast.distance(0, 199), 199)

    def test_rebuild_reroot(self):
        n = 25
        edges = random_tree(n, random.Random(9))
        fast = LCA(n, edges, root=0)
        fast.rebuild(root=7)
        naive = NaiveLCA(n, edges, root=7)
        rng = random.Random(99)
        pairs = [(rng.randrange(n), rng.randrange(n)) for _ in range(500)]
        self.assert_agree(fast, naive, pairs)
        self.assertEqual(fast.depth(7), 0)

    # ---------------- 非法输入 ---------------- #

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            LCA(0, [])
        with self.assertRaises(ValueError):
            LCA(3, [(0, 1, 0)])            # 边数不对
        with self.assertRaises(ValueError):
            LCA(3, [(0, 1, 0), (0, 1, 0)], root=0)  # 不连通（节点2孤立）
        fast = LCA(2, [(0, 1, 0)])
        with self.assertRaises(ValueError):
            fast.lca(0, 2)
        with self.assertRaises(ValueError):
            fast.add_leaf(5)
        with self.assertRaises(ValueError):
            LCA(2, [(0, 1, 0)], root=2)


def rng_weight(i):
    return (i * 7) % 13 - 6


if __name__ == "__main__":
    unittest.main(verbosity=2)
