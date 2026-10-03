"""polygon_self_intersection 的单元测试（标准库 unittest）。"""

import unittest
from fractions import Fraction

from polygon_self_intersection import (
    find_self_intersections,
    normalize_polygon,
    segment_intersection,
    suggest_repairs,
)


def edge_pairs(result):
    return sorted(item["edges"] for item in result["intersections"])


class TestSegmentIntersection(unittest.TestCase):
    def test_cross(self):
        hit = segment_intersection((0, 0), (2, 2), (0, 2), (2, 0))
        self.assertEqual(hit["kind"], "cross")
        self.assertEqual(hit["points"], [(Fraction(1), Fraction(1))])

    def test_no_intersection(self):
        self.assertIsNone(segment_intersection((0, 0), (1, 0), (0, 1), (1, 1)))

    def test_collinear_overlap(self):
        hit = segment_intersection((0, 0), (4, 0), (2, 0), (6, 0))
        self.assertEqual(hit["kind"], "overlap")
        self.assertEqual(hit["points"],
                         [(Fraction(2), Fraction(0)), (Fraction(4), Fraction(0))])

    def test_collinear_touch_point(self):
        hit = segment_intersection((0, 0), (2, 0), (2, 0), (5, 0))
        self.assertEqual(hit["kind"], "touch")
        self.assertEqual(hit["points"], [(Fraction(2), Fraction(0))])

    def test_collinear_disjoint(self):
        self.assertIsNone(segment_intersection((0, 0), (1, 0), (2, 0), (3, 0)))

    def test_endpoint_touch(self):
        hit = segment_intersection((0, 0), (1, 1), (1, 1), (2, 0))
        self.assertEqual(hit["kind"], "touch")
        self.assertEqual(hit["points"], [(Fraction(1), Fraction(1))])

    def test_vertical_horizontal_cross(self):
        hit = segment_intersection((1, -1), (1, 1), (0, 0), (2, 0))
        self.assertEqual(hit["kind"], "cross")
        self.assertEqual(hit["points"], [(Fraction(1), Fraction(0))])

    def test_float_input_exact(self):
        # 0.1+0.2 这类浮点误差不应影响判定：两条线仅在 (0.3, 0) 处相接
        hit = segment_intersection((0.0, 0.0), (0.3, 0.0), (0.3, 0.0), (0.3, 1.0))
        self.assertEqual(hit["kind"], "touch")


class TestNormalize(unittest.TestCase):
    def test_consecutive_duplicates_removed(self):
        vertices, removed = normalize_polygon(
            [(0, 0), (0, 0), (1, 0), (1, 1), (1, 1), (0, 1)])
        self.assertEqual(len(vertices), 4)
        self.assertEqual(removed, [1, 4])

    def test_closing_duplicate_removed(self):
        vertices, removed = normalize_polygon(
            [(0, 0), (1, 0), (1, 1), (0, 0)])
        self.assertEqual(len(vertices), 3)
        self.assertEqual(removed, [3])

    def test_all_same_points(self):
        vertices, removed = normalize_polygon([(2, 2), (2, 2), (2, 2)])
        self.assertEqual(len(vertices), 1)
        self.assertEqual(removed, [1, 2])


class TestSimplePolygons(unittest.TestCase):
    def test_square(self):
        result = find_self_intersections(
            [(0, 0), (4, 0), (4, 4), (0, 4)])
        self.assertEqual(result["intersections"], [])
        self.assertEqual(result["point_count"], 0)

    def test_concave_polygon(self):
        result = find_self_intersections(
            [(0, 0), (4, 0), (4, 4), (2, 1), (0, 4)])
        self.assertEqual(result["intersections"], [])

    def test_adjacent_edges_not_reported(self):
        # 尖刺 spike：相邻边共线回折，共享端点，不得误报
        result = find_self_intersections(
            [(0, 0), (4, 0), (4, 4), (2, 2), (0, 4)])
        self.assertEqual(result["intersections"], [])


class TestSelfIntersecting(unittest.TestCase):
    def test_bowtie(self):
        result = find_self_intersections(
            [(0, 0), (2, 2), (2, 0), (0, 2)])
        self.assertEqual(edge_pairs(result), [(0, 2)])
        item = result["intersections"][0]
        self.assertEqual(item["kind"], "cross")
        self.assertEqual(item["points"], [(Fraction(1), Fraction(1))])
        self.assertEqual(result["point_count"], 1)

    def test_pentagram(self):
        import math
        # 五角星 {5/2}：5 个交点，边对 (0,2)(0,3)(1,3)(1,4)(2,4)
        points = []
        for k in range(5):
            angle = math.pi / 2 + 2 * math.pi * 2 * k / 5
            points.append((round(math.cos(angle), 12),
                           round(math.sin(angle), 12)))
        result = find_self_intersections(points)
        self.assertEqual(edge_pairs(result),
                         [(0, 2), (0, 3), (1, 3), (1, 4), (2, 4)])
        self.assertEqual(result["point_count"], 5)

    def test_vertex_self_touch(self):
        # 8 字形在非相邻顶点处自触：顶点 (2,0) 出现两次
        result = find_self_intersections(
            [(0, 0), (2, 0), (2, 2), (0, 2), (2, 0), (2, -2), (0, -2)])
        pairs = edge_pairs(result)
        self.assertTrue(any(
            item["kind"] == "touch" and (Fraction(2), Fraction(0)) in item["points"]
            for item in result["intersections"]), pairs)


class TestCollinearOverlap(unittest.TestCase):
    def test_non_adjacent_overlap(self):
        # 边1 (4,0)->(4,1) 无关；构造底边被非相邻边部分重叠
        result = find_self_intersections(
            [(0, 0), (4, 0), (4, 2), (3, 2), (3, 0), (1, 0), (1, 2), (0, 2)])
        overlaps = [item for item in result["intersections"]
                    if item["kind"] == "overlap"]
        self.assertTrue(overlaps)
        # 边0 (0,0)-(4,0) 与 边4 (3,0)-(1,0) 重叠区间 (1,0)-(3,0)
        item = overlaps[0]
        self.assertEqual(item["edges"], (0, 4))
        self.assertEqual(item["points"],
                         [(Fraction(1), Fraction(0)), (Fraction(3), Fraction(0))])


class TestDegenerateInputs(unittest.TestCase):
    def test_empty(self):
        result = find_self_intersections([])
        self.assertEqual(result["intersections"], [])
        self.assertTrue(result["degenerate"]["too_few_vertices"])

    def test_single_point(self):
        result = find_self_intersections([(1, 1)])
        self.assertEqual(result["intersections"], [])

    def test_two_points(self):
        result = find_self_intersections([(0, 0), (1, 1)])
        self.assertEqual(result["intersections"], [])

    def test_zero_length_edges_ignored(self):
        # 正方形中插入重复点（零长度边），不得产生任何相交报告
        result = find_self_intersections(
            [(0, 0), (0, 0), (4, 0), (4, 0), (4, 4), (0, 4), (0, 4)])
        self.assertEqual(result["intersections"], [])
        self.assertEqual(result["removed_duplicates"], [1, 3, 6])

    def test_all_points_identical(self):
        result = find_self_intersections([(3, 3), (3, 3), (3, 3)])
        self.assertEqual(result["intersections"], [])
        self.assertTrue(result["degenerate"]["too_few_vertices"])

    def test_closed_ring_duplicate(self):
        result = find_self_intersections(
            [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)])
        self.assertEqual(result["intersections"], [])
        self.assertEqual(result["removed_duplicates"], [4])


class TestRepairSuggestions(unittest.TestCase):
    def test_simple_polygon_no_issue(self):
        suggestions = suggest_repairs([(0, 0), (1, 0), (1, 1), (0, 1)])
        self.assertEqual(suggestions, ["未发现问题，多边形为简单多边形。"])

    def test_bowtie_suggestion_mentions_edges_and_point(self):
        suggestions = suggest_repairs([(0, 0), (2, 2), (2, 0), (0, 2)])
        text = "\n".join(suggestions)
        self.assertIn("边 0 与边 2", text)
        self.assertIn("(1, 1)", text)

    def test_duplicate_suggestion(self):
        suggestions = suggest_repairs([(0, 0), (0, 0), (1, 0), (1, 1), (0, 1)])
        self.assertIn("重复顶点", suggestions[0])

    def test_overlap_suggestion(self):
        suggestions = suggest_repairs(
            [(0, 0), (4, 0), (4, 2), (3, 2), (3, 0), (1, 0), (1, 2), (0, 2)])
        text = "\n".join(suggestions)
        self.assertIn("共线重叠", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
