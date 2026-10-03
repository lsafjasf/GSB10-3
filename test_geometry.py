"""test_geometry.py — geometry 库自测与对拍（python3 -m unittest -v）"""

import math
import random
import unittest

from geometry import (
    EPS, convex_hull, signed_area, area, is_ccw, ensure_ccw,
    diameter_pairs, diameter_bruteforce,
    min_area_rect, min_area_rect_bruteforce,
)


def circle_points(n, r=10.0, phase=0.0):
    return [(r * math.cos(phase + 2 * math.pi * i / n),
             r * math.sin(phase + 2 * math.pi * i / n)) for i in range(n)]


class TestConvexHull(unittest.TestCase):
    def test_duplicate_points(self):
        pts = [(0, 0), (1, 0), (1, 0), (0, 1), (0, 0), (1, 1)]
        h = convex_hull(pts)
        self.assertEqual(len(h), len(set(h)), "凸包中不应有重合点")
        self.assertAlmostEqual(area(h), 1.0)  # 单位正方形

    def test_collinear_dropped_by_default(self):
        """默认策略：共线边上的中间点被丢弃，只留端点。"""
        pts = [(0, 0), (1, 0), (2, 0), (3, 0), (1, 2), (2, 2), (3, 3)]
        h = convex_hull(pts)
        # (1,0),(2,0) 与底边共线被丢弃；(2,2) 是内部点；(1,2) 是顶点
        self.assertEqual(set(h), {(0, 0), (3, 0), (3, 3), (1, 2)})

    def test_collinear_kept_when_requested(self):
        """keep_collinear=True：边界上的共线点全部保留。"""
        pts = [(0, 0), (1, 0), (2, 0), (3, 0), (1, 2), (2, 2), (3, 3)]
        h = convex_hull(pts, keep_collinear=True)
        # (2, 2) 是严格内部点，不在边界上；边界共线点全部保留
        self.assertEqual(set(h), set(pts) - {(2, 2)})
        # 两种策略面积必须一致
        self.assertAlmostEqual(area(h), area(convex_hull(pts)))

    def test_all_collinear(self):
        pts = [(i, 2 * i) for i in range(10)]
        h = convex_hull(pts)
        self.assertEqual(h, [(0, 0), (9, 18)], "全部共线时应只剩两个端点")
        self.assertAlmostEqual(area(h), 0.0)

    def test_all_identical(self):
        h = convex_hull([(5, 5)] * 7)
        self.assertEqual(h, [(5, 5)])

    def test_hull_is_ccw(self):
        random.seed(1)
        for _ in range(50):
            pts = [(random.uniform(-100, 100), random.uniform(-100, 100))
                   for _ in range(30)]
            h = convex_hull(pts)
            if len(h) >= 3:
                self.assertTrue(is_ccw(h), "凸包必须为逆时针朝向")
                self.assertGreater(signed_area(h), 0)

    def test_known_area(self):
        h = convex_hull([(0, 0), (4, 0), (4, 3), (0, 3), (2, 1)])
        self.assertAlmostEqual(signed_area(h), 12.0)


class TestOrientationAndArea(unittest.TestCase):
    """多边形朝向与面积的校验断言。"""

    def test_orientation_flip(self):
        ccw = [(0, 0), (4, 0), (4, 3), (0, 3)]
        cw = ccw[::-1]
        self.assertGreater(signed_area(ccw), 0)
        self.assertLess(signed_area(cw), 0)
        self.assertAlmostEqual(signed_area(ccw), -signed_area(cw))
        fixed = ensure_ccw(cw)
        self.assertTrue(is_ccw(fixed))
        self.assertAlmostEqual(area(fixed), 12.0)

    def test_shoelace_known_values(self):
        self.assertAlmostEqual(area([(0, 0), (1, 0), (0, 1)]), 0.5)
        self.assertAlmostEqual(area(circle_points(360, r=2.0)),
                               math.pi * 4, delta=1e-3)

    def test_rect_corners_ccw_and_area(self):
        random.seed(2)
        for _ in range(30):
            pts = [(random.uniform(-50, 50), random.uniform(-50, 50))
                   for _ in range(20)]
            r = min_area_rect(pts)
            self.assertGreaterEqual(signed_area(r["corners"]), 0,
                                    "矩形角点应为 CCW")
            self.assertAlmostEqual(area(r["corners"]), r["area"], places=6)
            self.assertAlmostEqual(r["width"] * r["height"], r["area"], places=6)


class TestDiameter(unittest.TestCase):
    def check(self, pts):
        d_fast, pairs = diameter_pairs(pts)
        d_slow, _ = diameter_bruteforce(pts)
        self.assertAlmostEqual(d_fast, d_slow, places=9,
                               msg=f"对拍失败: {pts!r}")
        for p, q in pairs:
            self.assertAlmostEqual(math.hypot(p[0] - q[0], p[1] - q[1]),
                                   d_slow, places=9)

    def test_random_against_bruteforce(self):
        random.seed(42)
        for trial in range(200):
            n = random.randint(1, 40)
            pts = [(random.uniform(-100, 100), random.uniform(-100, 100))
                   for _ in range(n)]
            self.check(pts)

    def test_grid_with_duplicates(self):
        random.seed(7)
        for _ in range(100):
            pts = [(random.randint(0, 5), random.randint(0, 5))
                   for _ in range(random.randint(1, 20))]
            self.check(pts)

    def test_on_circle(self):
        for n in (3, 4, 5, 6, 17, 100):
            pts = circle_points(n, r=5.0)
            d, _ = diameter_pairs(pts)
            d_slow, _ = diameter_bruteforce(pts)
            self.assertAlmostEqual(d, d_slow, places=9)
            if n % 2 == 0:
                self.assertAlmostEqual(d, 10.0, places=9)  # 对径点 = 直径

    def test_edge_cases(self):
        self.assertEqual(diameter_pairs([(3, 4)])[0], 0.0)
        d, pairs = diameter_pairs([(0, 0), (3, 4)])
        self.assertAlmostEqual(d, 5.0)
        self.assertEqual(diameter_pairs([(1, 1)] * 5)[0], 0.0)
        d, _ = diameter_pairs([(i, i) for i in range(8)])  # 全部共线
        self.assertAlmostEqual(d, math.hypot(7, 7))
        with self.assertRaises(ValueError):
            diameter_pairs([])


class TestMinAreaRect(unittest.TestCase):
    def test_random_against_bruteforce(self):
        random.seed(123)
        for trial in range(300):
            n = random.randint(3, 30)
            pts = [(random.uniform(-100, 100), random.uniform(-100, 100))
                   for _ in range(n)]
            fast = min_area_rect(pts)["area"]
            slow = min_area_rect_bruteforce(pts)
            self.assertAlmostEqual(fast, slow, places=7,
                                   msg=f"对拍失败: {pts!r}")

    def test_known_square(self):
        r = min_area_rect([(0, 0), (2, 0), (2, 2), (0, 2)])
        self.assertAlmostEqual(r["area"], 4.0)

    def test_rotated_square(self):
        pts = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        r = min_area_rect(pts)
        self.assertAlmostEqual(r["area"], 2.0)  # 边长 sqrt(2)

    def test_rect_contains_all_points(self):
        random.seed(9)
        for _ in range(50):
            pts = [(random.uniform(-50, 50), random.uniform(-50, 50))
                   for _ in range(25)]
            r = min_area_rect(pts)
            ca, sa = math.cos(-r["angle"]), math.sin(-r["angle"])
            rot = [(x * ca - y * sa, x * sa + y * ca) for x, y in pts]
            rc = [(x * ca - y * sa, x * sa + y * ca) for x, y in r["corners"]]
            xs = [p[0] for p in rc]; ys = [p[1] for p in rc]
            for x, y in rot:
                self.assertGreaterEqual(x, min(xs) - 1e-7)
                self.assertLessEqual(x, max(xs) + 1e-7)
                self.assertGreaterEqual(y, min(ys) - 1e-7)
                self.assertLessEqual(y, max(ys) + 1e-7)

    def test_edge_cases(self):
        r = min_area_rect([(2, 3)])
        self.assertEqual(r["area"], 0.0)
        r = min_area_rect([(0, 0), (3, 4)])
        self.assertEqual(r["area"], 0.0)
        self.assertAlmostEqual(r["width"], 5.0)
        r = min_area_rect([(i, i) for i in range(6)])  # 全部共线
        self.assertAlmostEqual(r["area"], 0.0)
        r = min_area_rect([(1, 1)] * 4)  # 全部重合
        self.assertEqual(r["area"], 0.0)
        with self.assertRaises(ValueError):
            min_area_rect([])

    def test_on_circle(self):
        # 圆上均匀 4n 个点：最小矩形应接近外切正方形面积 (2r)^2
        pts = circle_points(400, r=1.0)
        r = min_area_rect(pts)
        self.assertAlmostEqual(r["area"], 4.0, delta=1e-3)
        slow = min_area_rect_bruteforce(pts)
        self.assertAlmostEqual(r["area"], slow, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
