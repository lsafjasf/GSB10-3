# -*- coding: utf-8 -*-
"""marching_squares 自测（unittest，仅标准库）。

运行：python3 test_marching_squares.py [-v]
"""

import random
import unittest

from marching_squares import assert_connected, extract_contours


class TestTrivialFields(unittest.TestCase):
    """全部高于 / 全部低于阈值：不应产生任何等值线。"""

    def test_all_above(self):
        field = [[1.0] * 6 for _ in range(6)]
        r = extract_contours(field, 0.0)
        self.assertEqual(r.stats["segments"], 0)
        self.assertEqual(r.closed_count + r.open_count, 0)
        assert_connected(r)

    def test_all_below(self):
        field = [[-1.0] * 6 for _ in range(6)]
        r = extract_contours(field, 0.0)
        self.assertEqual(r.stats["segments"], 0)
        self.assertEqual(r.closed_count + r.open_count, 0)
        assert_connected(r)


class TestClosedContour(unittest.TestCase):
    """场中央一个孤立峰：应得到一条闭合等值线，不穿边界。"""

    def test_single_peak(self):
        field = [[0.0] * 7 for _ in range(7)]
        field[3][3] = 1.0
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.closed_count, 1)
        self.assertEqual(r.open_count, 0)
        self.assertEqual(r.stats["segments"], 4)   # 菱形，4 条线段
        self.assertEqual(len(r.lines[0]), 4)

    def test_two_peaks_two_loops(self):
        field = [[0.0] * 9 for _ in range(5)]
        field[2][2] = 1.0
        field[2][6] = 1.0
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.closed_count, 2)
        self.assertEqual(r.open_count, 0)


class TestOpenContourBoundary(unittest.TestCase):
    """高值区触及边界：等值线两端落在边界上，为开放线。"""

    def test_half_plane(self):
        # 左半高右半低 -> 一条竖直等值线，两端在上下边界
        field = [[1.0 if i < 2 else 0.0 for i in range(5)] for _ in range(5)]
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.open_count, 1)
        self.assertEqual(r.closed_count, 0)
        line = r.lines[0]
        ys = sorted(p[1] for p in line)
        self.assertEqual(ys[0], 0.0)      # 下端点在下边界
        self.assertEqual(ys[-1], 4.0)     # 上端点在上边界
        for x, _ in line:
            self.assertAlmostEqual(x, 1.5)

    def test_corner_region(self):
        # 仅左下角高 -> 一条开放线，两端分别落在左、下边界
        field = [[0.0] * 5 for _ in range(5)]
        field[0][0] = 1.0
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.open_count, 1)
        self.assertEqual(r.closed_count, 0)
        pts = r.lines[0]
        ends = (pts[0], pts[-1])
        self.assertTrue(any(abs(x) < 1e-9 for x, _ in ends))
        self.assertTrue(any(abs(y) < 1e-9 for _, y in ends))


class TestSaddleDisambiguation(unittest.TestCase):
    """鞍点（歧义格）消歧：规则一致、可统计、连接不断。"""

    def test_single_saddle_cell_join(self):
        # 2x2 场：BL、TR 高 -> case 5；中心值 0.5 >= 0.5 -> 高角连通
        field = [[1.0, 0.0], [0.0, 1.0]]
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.stats["ambiguous_cells"], 1)
        self.assertEqual(r.stats["ambiguous_join_high"], 1)
        self.assertEqual(r.stats["ambiguous_separate_high"], 0)
        self.assertEqual(r.open_count, 2)   # 两条开放线，各接两段边界
        self.assertEqual(r.closed_count, 0)

    def test_single_saddle_cell_separate(self):
        # 中心值 (1-0.6-0.6+1)/4 = 0.2 < 0.5 -> 高角分离
        field = [[1.0, -0.6], [-0.6, 1.0]]
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.stats["ambiguous_cells"], 1)
        self.assertEqual(r.stats["ambiguous_join_high"], 0)
        self.assertEqual(r.stats["ambiguous_separate_high"], 1)
        self.assertEqual(r.open_count, 2)

    def test_checkerboard_all_saddles(self):
        # 3x3 棋盘场：4 个格全是歧义格，验证一致消歧下仍无断点
        field = [[0.0, 1.0, 0.0],
                 [1.0, 0.0, 1.0],
                 [0.0, 1.0, 0.0]]
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.stats["ambiguous_cells"], 4)
        self.assertEqual(r.stats["segments"], 8)
        self.assertGreaterEqual(r.closed_count + r.open_count, 1)

    def test_saddle_topology_consistency(self):
        # 同一鞍点场整体取反 + 阈值取反，拓扑（条数）应镜像一致
        field = [[1.0, 0.0], [0.0, 1.0]]
        neg = [[-v for v in row] for row in field]
        r1 = extract_contours(field, 0.5)
        r2 = extract_contours(neg, -0.5)
        assert_connected(r1)
        assert_connected(r2)
        self.assertEqual(r1.closed_count + r1.open_count,
                         r2.closed_count + r2.open_count)


class TestConnectivityRandom(unittest.TestCase):
    """随机场多阈值：断点数恒为零，线段恰好全部接入折线。"""

    def test_random_fields(self):
        rng = random.Random(20261004)
        for trial in range(20):
            nx, ny = 12, 9
            field = [[rng.uniform(-1.0, 1.0) for _ in range(nx)]
                     for _ in range(ny)]
            for level in (-0.5, 0.0, 0.5):
                r = extract_contours(field, level)
                assert_connected(r)
                self.assertEqual(r.stats["break_points"], 0)

    def test_exact_level_values(self):
        # 采样值恰等于阈值：触发格点归并逻辑，仍须零断点
        field = [[0.0, 0.5, 0.0],
                 [0.5, 0.0, 0.5],
                 [0.0, 0.5, 0.0]]
        r = extract_contours(field, 0.5)
        assert_connected(r)
        self.assertEqual(r.stats["break_points"], 0)


class TestValidation(unittest.TestCase):
    def test_too_small(self):
        with self.assertRaises(ValueError):
            extract_contours([[1.0]], 0.0)

    def test_ragged(self):
        with self.assertRaises(ValueError):
            extract_contours([[0.0, 1.0], [0.0]], 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
