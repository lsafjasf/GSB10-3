"""closest_pair 的自测：边界用例 + 与暴力实现的随机对拍。

运行：python3 test_closest_pair.py  （或 python3 -m unittest test_closest_pair -v）
"""

import math
import random
import unittest

from closest_pair import closest_pairs, closest_pairs_bruteforce


class EdgeCaseTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(closest_pairs([]), (None, []))

    def test_single_point(self):
        self.assertEqual(closest_pairs([(3, 4)]), (None, []))

    def test_two_points(self):
        d, pairs = closest_pairs([(0, 0), (3, 4)])
        self.assertAlmostEqual(d, 5.0)
        self.assertEqual(pairs, [((0, 0), (3, 4))])

    def test_all_coincident(self):
        # 全部重合：最小距离 0，坐标去重后只有 1 个点对
        d, pairs = closest_pairs([(7, 7)] * 1000)
        self.assertEqual(d, 0.0)
        self.assertEqual(pairs, [((7, 7), (7, 7))])

    def test_partial_coincident(self):
        pts = [(1, 1), (1, 1), (1, 1), (5, 5)]
        d, pairs = closest_pairs(pts)
        self.assertEqual(d, 0.0)
        self.assertEqual(pairs, [((1, 1), (1, 1))])

    def test_collinear_horizontal(self):
        pts = [(x, 0) for x in range(100)]  # 相邻间距 1，共 99 对
        d, pairs = closest_pairs(pts)
        self.assertEqual(d, 1.0)
        self.assertEqual(len(pairs), 99)
        self.assertIn(((0, 0), (1, 0)), pairs)
        self.assertIn(((98, 0), (99, 0)), pairs)

    def test_collinear_vertical(self):
        pts = [(2, y) for y in range(-50, 50)]
        d, pairs = closest_pairs(pts)
        self.assertEqual(d, 1.0)
        self.assertEqual(len(pairs), 99)

    def test_collinear_diagonal(self):
        pts = [(k, 2 * k) for k in range(60)]  # 相邻距离 sqrt(5)
        d, pairs = closest_pairs(pts)
        self.assertAlmostEqual(d, math.sqrt(5))
        self.assertEqual(len(pairs), 59)

    def test_extreme_coordinates(self):
        big = 1e15
        pts = [(-big, -big), (big, big), (big, -big), (-big, big), (big, big - 1)]
        d, pairs = closest_pairs(pts)
        self.assertEqual(d, 1.0)
        self.assertEqual(pairs, [((big, big - 1), (big, big))])

    def test_extreme_mixed_tiny_and_huge(self):
        pts = [(1e-12, 0.0), (2e-12, 0.0), (1e15, 1e15)]
        d, pairs = closest_pairs(pts)
        self.assertAlmostEqual(d, 1e-12)
        self.assertEqual(len(pairs), 1)

    def test_negative_coordinates(self):
        pts = [(-5, -5), (-4, -5), (100, 100)]
        d, pairs = closest_pairs(pts)
        self.assertEqual(d, 1.0)
        self.assertEqual(pairs, [((-5, -5), (-4, -5))])

    def test_all_ties_grid(self):
        # 3x3 单位网格：12 条边都是最近点对
        pts = [(x, y) for x in range(3) for y in range(3)]
        d, pairs = closest_pairs(pts)
        self.assertEqual(d, 1.0)
        self.assertEqual(len(pairs), 12)

    def test_multiple_ties_scattered(self):
        pts = [(0, 0), (1, 0), (10, 10), (11, 10), (50, 0), (50, 1)]
        d, pairs = closest_pairs(pts)
        self.assertEqual(d, 1.0)
        self.assertEqual(len(pairs), 3)

    def test_known_answer(self):
        pts = [(2, 3), (12, 30), (40, 50), (5, 1), (12, 10), (3, 4)]
        d, pairs = closest_pairs(pts)
        self.assertAlmostEqual(d, math.sqrt(2))
        self.assertEqual(pairs, [((2, 3), (3, 4))])

    def test_accepts_list_points(self):
        d, pairs = closest_pairs([[0, 0], [0, 2], [9, 9]])
        self.assertEqual(d, 2.0)
        self.assertEqual(pairs, [((0, 0), (0, 2))])


class DifferentialTests(unittest.TestCase):
    """分治结果必须与暴力完全一致（距离 + 全部最近点对）。"""

    def check(self, pts):
        d_fast, pairs_fast = closest_pairs(pts)
        d_slow, pairs_slow = closest_pairs_bruteforce(pts)
        if d_slow is None:
            self.assertIsNone(d_fast)
            return
        # 平方距离比较，避免浮点开方误差影响判断
        self.assertAlmostEqual(d_fast * d_fast, d_slow * d_slow, places=9)
        self.assertEqual(pairs_fast, pairs_slow)

    def test_random_float(self):
        rng = random.Random(20261004)
        for trial in range(150):
            n = rng.randint(2, 120)
            pts = [(rng.uniform(-1000, 1000), rng.uniform(-1000, 1000))
                   for _ in range(n)]
            self.check(pts)

    def test_random_int_small_range(self):
        # 小范围整数坐标：大量重合与并列最小距离，专门压测"输出全部点对"
        rng = random.Random(42)
        for trial in range(150):
            n = rng.randint(2, 150)
            pts = [(rng.randint(0, 8), rng.randint(0, 8)) for _ in range(n)]
            self.check(pts)

    def test_random_collinear(self):
        rng = random.Random(7)
        for trial in range(50):
            n = rng.randint(2, 200)
            xs = sorted(rng.randint(0, 500) for _ in range(n))
            pts = [(x, 3 * x + 1) for x in xs]
            self.check(pts)

    def test_random_clusters(self):
        rng = random.Random(99)
        for trial in range(50):
            centers = [(rng.uniform(-1e6, 1e6), rng.uniform(-1e6, 1e6))
                       for _ in range(5)]
            pts = []
            for _ in range(rng.randint(10, 200)):
                cx, cy = rng.choice(centers)
                pts.append((cx + rng.gauss(0, 1), cy + rng.gauss(0, 1)))
            self.check(pts)

    def test_random_extreme_magnitude(self):
        rng = random.Random(31337)
        for trial in range(50):
            n = rng.randint(2, 100)
            pts = [(rng.uniform(-1e15, 1e15), rng.uniform(-1e15, 1e15))
                   for _ in range(n)]
            self.check(pts)

    def test_larger_instance(self):
        rng = random.Random(2026)
        pts = [(rng.uniform(0, 1e6), rng.uniform(0, 1e6)) for _ in range(3000)]
        self.check(pts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
