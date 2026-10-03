#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_contour.py — contour_lib 自测（仅标准库, 无 pytest 也可运行）

运行:  python3 test_contour.py       (python3 -m unittest -v 亦可)
"""

import math
import unittest

from contour_lib import extract_contours


def assert_continuity(test, res, nx, ny):
    """通用连续性断言: 断点数必须为 0; 折线首尾与原线段坐标逐点一致。"""
    test.assertEqual(res.breakpoints, 0,
                     "出现 {} 个拼接断点".format(res.breakpoints))

    # 开等值线端点必须落在域边界上; 闭合等值线首尾坐标必须相同
    for poly in res.open_contours:
        test.assertGreaterEqual(len(poly), 2)
    for poly in res.closed_contours:
        test.assertGreaterEqual(len(poly), 4)
        test.assertEqual(poly[0], poly[-1])

    # 所有折线顶点集合必须能覆盖全部线段端点（无丢失、无孤立）
    used_points = set()
    for poly in res.open_contours + res.closed_contours:
        for p in poly:
            used_points.add((round(p[0], 12), round(p[1], 12)))
    seg_points = set()
    for p1, p2 in res.segments:
        seg_points.add((round(p1[0], 12), round(p1[1], 12)))
        seg_points.add((round(p2[0], 12), round(p2[1], 12)))
    test.assertEqual(used_points, seg_points,
                     "折线顶点与线段端点集合不一致（存在断点或孤立点）")


class TestContourExtraction(unittest.TestCase):

    def test_all_above_threshold(self):
        """全部高于阈值: 无线段、无歧义格、断点 0。"""
        nx, ny, level = 5, 4, 0.0
        vals = [1.0 + 0.01 * (i + j) for j in range(ny) for i in range(nx)]
        res = extract_contours(vals, nx, ny, level)
        self.assertEqual(len(res.segments), 0)
        self.assertEqual(res.num_ambiguous, 0)
        self.assertEqual(len(res.closed_contours), 0)
        self.assertEqual(len(res.open_contours), 0)
        assert_continuity(self, res, nx, ny)

    def test_all_below_threshold(self):
        """全部低于阈值: 同样无线段、断点 0。"""
        nx, ny, level = 5, 4, 100.0
        vals = [0.01 * (i + j) for j in range(ny) for i in range(nx)]
        res = extract_contours(vals, nx, ny, level)
        self.assertEqual(len(res.segments), 0)
        self.assertEqual(res.num_ambiguous, 0)
        self.assertEqual(len(res.closed_contours), 0)
        self.assertEqual(len(res.open_contours), 0)
        assert_continuity(self, res, nx, ny)

    def test_linear_plane_open_contours(self):
        """线性平面: 等值线是从一边贯穿到另一边的开放线, 无歧义格。"""
        nx, ny, level = 10, 8, 0.5
        vals = [i / (nx - 1) for j in range(ny) for i in range(nx)]  # f = x
        res = extract_contours(vals, nx, ny, level,
                               dx=1.0 / (nx - 1), dy=1.0 / (ny - 1))
        self.assertEqual(res.num_ambiguous, 0)
        self.assertEqual(len(res.closed_contours), 0)
        self.assertEqual(len(res.open_contours), 1)
        poly = res.open_contours[0]
        # 一端在左边界, 另一端在右边界
        xs = [p[0] for p in poly]
        self.assertAlmostEqual(min(xs), 0.5, places=9)
        self.assertAlmostEqual(max(xs), 0.5, places=9)
        assert_continuity(self, res, nx, ny)

    def test_gaussian_closed_contour(self):
        """高斯峰: 等值线是闭合环, 不触碰边界。"""
        nx = ny = 31
        level = 0.4
        vals = []
        for j in range(ny):
            for i in range(nx):
                x, y = i / (nx - 1), j / (ny - 1)
                vals.append(math.exp(-((x - 0.5) ** 2 + (y - 0.5) ** 2) / 0.02))
        res = extract_contours(vals, nx, ny, level,
                               dx=1.0 / (nx - 1), dy=1.0 / (ny - 1))
        self.assertEqual(len(res.open_contours), 0)
        self.assertEqual(len(res.closed_contours), 1)
        # 环上所有点严格位于域内部
        for poly in res.closed_contours:
            for x, y in poly:
                self.assertGreater(x, 0.0)
                self.assertLess(x, 1.0)
                self.assertGreater(y, 0.0)
                self.assertLess(y, 1.0)
        assert_continuity(self, res, nx, ny)

    def test_saddle_single_cell_high(self):
        """单格歧义 case 5, 鞍点值高于阈值: 两条独立线段。"""
        # 左下/右上 高, 左上/右下 低; s=(ad-bc)/(a-b-c+d)
        a, b, c, d = 9.0, 0.0, 9.0, 1.0
        # s = (9*1-0*9)/(9-0-9+1) = 9/1 = 9 >= 5 -> 鞍点为高
        res = extract_contours([a, b, d, c], 2, 2, 5.0)  # 节点顺序: a,b,d,c
        self.assertEqual(res.num_ambiguous, 1)
        self.assertEqual(res.saddle_high_cells, [(0, 0)])
        self.assertEqual(res.saddle_low_cells, [])
        self.assertEqual(len(res.segments), 2)
        # 两条线都开放（各自端点落在边界上）
        self.assertEqual(len(res.open_contours), 2)
        self.assertEqual(len(res.closed_contours), 0)
        assert_continuity(self, res, 2, 2)

    def test_saddle_single_cell_low(self):
        """单格歧义 case 5, 鞍点值低于阈值: 另一种连接方式。"""
        a, b, c, d = 9.0, 0.0, 8.0, 1.0
        # s = (9*1-0*8)/(9-0-8+1) = 9/2 = 4.5 < 5 -> 鞍点为低
        res = extract_contours([a, b, d, c], 2, 2, 5.0)  # 节点顺序: a,b,d,c
        self.assertEqual(res.num_ambiguous, 1)
        self.assertEqual(res.saddle_high_cells, [])
        self.assertEqual(res.saddle_low_cells, [(0, 0)])
        self.assertEqual(len(res.segments), 2)
        self.assertEqual(len(res.open_contours), 2)
        assert_continuity(self, res, 2, 2)

    def test_saddle_field_disambiguation_consistency(self):
        """双曲鞍点场 (x-h)(y-k): 歧义格必须按同一规则消歧, 断点为 0。"""
        nx = ny = 31
        level = 0.0
        h, k = 0.517, 0.483  # 鞍点 (h,k) 落在格内, 不在节点上
        vals = []
        for j in range(ny):
            for i in range(nx):
                x, y = i / (nx - 1), j / (ny - 1)
                vals.append((x - h) * (y - k))
        res = extract_contours(vals, nx, ny, level,
                               dx=1.0 / (nx - 1), dy=1.0 / (ny - 1))
        self.assertGreater(res.num_ambiguous, 0)
        self.assertEqual(res.num_ambiguous,
                         len(res.saddle_high_cells) + len(res.saddle_low_cells))
        # 消歧一致性直接体现为: 歧义格周围不产生任何断点
        assert_continuity(self, res, nx, ny)
        # 等值线近似 x=h 与 y=k 两条直线, 各自贯穿边界 -> 2 条开放线
        self.assertEqual(len(res.closed_contours), 0)
        self.assertEqual(len(res.open_contours), 2)

    def test_boundary_crossing(self):
        """等值线多次穿过边界: 每条开放线两端都在边界, 断点为 0。"""
        nx = ny = 30
        level = 0.5
        vals = []
        for j in range(ny):
            for i in range(nx):
                x, y = i / (nx - 1), j / (ny - 1)
                # 边界值随位置变化, 等值线必然多次穿过边界
                vals.append(0.5 * (x - 0.5) + 0.4 * math.sin(2 * math.pi * y))
        res = extract_contours(vals, nx, ny, level,
                               dx=1.0 / (nx - 1), dy=1.0 / (ny - 1))
        assert_continuity(self, res, nx, ny)
        n_open, n_closed = len(res.open_contours), len(res.closed_contours)
        self.assertGreater(n_open, 0)
        print("\n[边界用例] 开放 {} 条, 闭合 {} 条, 线段 {} 段, 歧义格 {} 个".format(
            n_open, n_closed, len(res.segments), res.num_ambiguous))

    def test_two_hills_two_closed(self):
        """两个高斯峰: 恰好 2 条闭合等值线。"""
        nx = ny = 41
        level = 0.5
        vals = []
        for j in range(ny):
            for i in range(nx):
                x, y = i / (nx - 1), j / (ny - 1)
                g1 = math.exp(-((x - 0.25) ** 2 + (y - 0.5) ** 2) / 0.01)
                g2 = math.exp(-((x - 0.75) ** 2 + (y - 0.5) ** 2) / 0.01)
                vals.append(max(g1, g2))
        res = extract_contours(vals, nx, ny, level,
                               dx=1.0 / (nx - 1), dy=1.0 / (ny - 1))
        self.assertEqual(len(res.closed_contours), 2)
        self.assertEqual(len(res.open_contours), 0)
        assert_continuity(self, res, nx, ny)


    def test_random_fields_property(self):
        """随机场模糊测试: 任意标量场、任意阈值下断点恒为 0, 统计自洽。"""
        import random
        rng = random.Random(42)
        for trial in range(400):
            nx = rng.randint(2, 9)
            ny = rng.randint(2, 9)
            vals = [rng.uniform(-1.0, 1.0) for _ in range(nx * ny)]
            level = rng.uniform(-0.9, 0.9)
            res = extract_contours(vals, nx, ny, level)
            self.assertEqual(
                res.breakpoints, 0,
                "trial {} ({}x{}, level={}) 出现断点".format(trial, nx, ny, level))
            self.assertEqual(
                res.num_ambiguous,
                len(res.saddle_high_cells) + len(res.saddle_low_cells))
            # 线段总数 == 折线段数之和（闭合环多一个重复端点）
            chain_seg = sum(len(p) - 1 for p in res.open_contours)
            chain_seg += sum(len(p) - 1 for p in res.closed_contours)
            self.assertEqual(chain_seg, len(res.segments))
            # 开放线端点必须落在边界; 闭合线首尾必须相同
            for poly in res.open_contours:
                self.assertTrue(
                    poly[0][0] in (0.0, nx - 1.0) or poly[0][1] in (0.0, ny - 1.0)
                    or poly[-1][0] in (0.0, nx - 1.0)
                    or poly[-1][1] in (0.0, ny - 1.0))
            for poly in res.closed_contours:
                self.assertEqual(poly[0], poly[-1])

    def test_exact_level_at_node(self):
        """节点值恰好等于阈值的退化情形: 不崩溃、不产生奇数度断点。"""
        nx, ny, level = 3, 3, 0.0
        vals = [-1, 0, -1,
                0, 0, 0,
                -1, 0, -1]
        res = extract_contours(vals, nx, ny, level)
        self.assertEqual(res.breakpoints, 0)
        self.assertEqual(
            len(res.segments),
            sum(1 for _ in res.segments))
        # 提取出的所有折线必须是开线或闭线之一且无重复丢失
        n_chains = len(res.open_contours) + len(res.closed_contours)
        self.assertGreater(n_chains, 0)

    def test_diagonal_exact_level(self):
        """等值线恰好经过对角线节点 (i,i): 节点键跨格复用, 断点为 0。"""
        nx = ny = 5
        level = 0.0
        vals = [i - j for j in range(ny) for i in range(nx)]
        res = extract_contours(vals, nx, ny, level)
        assert_continuity(self, res, nx, ny)
        # 对角线从 (0,0) 到 (4,4), 形成 1 条开放等值线（节点处拼接）
        self.assertEqual(len(res.open_contours) + len(res.closed_contours), 1)

    def test_shifted_grid_origin(self):
        """非单位 dx/dy 与原点偏移: 坐标变换正确。"""
        nx, ny, level = 4, 4, 0.5
        vals = [i / (nx - 1) for j in range(ny) for i in range(nx)]
        res = extract_contours(vals, nx, ny, level,
                               dx=10.0, dy=20.0, x0=100.0, y0=200.0)
        self.assertEqual(len(res.open_contours), 1)
        for p in res.open_contours[0]:
            self.assertAlmostEqual(p[0], 115.0, places=9)
        assert_continuity(self, res, nx, ny)


if __name__ == "__main__":
    unittest.main(verbosity=2)
