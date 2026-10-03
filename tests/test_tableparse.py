"""tableparse 自测（标准库 unittest，运行：python3 -m unittest discover -s tests -v）"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tableparse import (
    Span,
    display_width,
    infer_boundaries,
    is_rule_line,
    parse,
    validate,
)


class TestDisplayWidth(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(display_width("abc"), 3)

    def test_fullwidth(self):
        self.assertEqual(display_width("姓名"), 4)          # 全角汉字
        self.assertEqual(display_width("ＡＢ１２"), 8)      # 全角字母数字
        self.assertEqual(display_width("a你b"), 4)          # 混排


class TestBoundaryInference(unittest.TestCase):
    def test_single_column_no_boundary(self):
        # 单列：任意两行拼不出贯通空白带，不应产生边界
        boundaries, _ = infer_boundaries(["苹果", "香蕉  水果", "梨"])
        self.assertEqual(boundaries, [])

    def test_gap_cluster(self):
        lines = ["name   age", "tom     10", "alice   9"]
        boundaries, profile = infer_boundaries(lines, tolerance=0)
        self.assertEqual(len(boundaries), 1)
        b = boundaries[0]
        # 边界必须落在每行都为空白的显示列上
        for c in range(b.start, b.end):
            self.assertEqual(profile[c], 3)

    def test_tolerance_allows_span_rows(self):
        # 第 2 行内容穿越空白带（跨列痕迹），tolerance>=1 时边界仍在
        lines = ["A   B", "longvalue", "C   D", "E   F"]
        strict, _ = infer_boundaries(lines, tolerance=0)
        self.assertEqual(strict, [])
        tolerant, _ = infer_boundaries(lines, tolerance=1)
        self.assertEqual(len(tolerant), 1)


class TestParseBasic(unittest.TestCase):
    def test_single_column(self):
        result = parse("苹果\n香蕉\n梨")
        self.assertEqual(result.n_cols, 1)
        self.assertEqual(result.rows, [["苹果"], ["香蕉"], ["梨"]])
        self.assertEqual(result.errors, [])

    def test_irregular_whitespace(self):
        # 列间空白宽度 2~7 不等、有前导空白、数值列长短不一
        text = (
            "name     age  city\n"
            "  tom    10   北京\n"
            "alice   9     上海\n"
        )
        result = parse(text)
        self.assertEqual(result.errors, [])
        self.assertEqual(
            result.rows,
            [["name", "age", "city"],
             ["tom", "10", "北京"],
             ["alice", "9", "上海"]],
        )

    def test_empty_cells(self):
        text = "a  b  c\n1     3\n   2  \n"
        result = parse(text, tolerance=0)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.rows[1], ["1", "", "3"])   # 中间空单元格
        self.assertEqual(result.rows[2], ["", "2", ""])    # 首尾空单元格
        for row in result.rows:
            self.assertEqual(len(row), 3)

    def test_fullwidth_alignment(self):
        # 全角姓名按显示宽度对齐，不能按字符数切
        text = "姓名    年龄  城市\n张三    18    北京\n李四四  20    上海"
        result = parse(text)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.rows[1], ["张三", "18", "北京"])
        self.assertEqual(result.rows[2], ["李四四", "20", "上海"])

    def test_rule_lines_skipped(self):
        text = "a  b\n---+---\n1  2\n===+===\n3  4\n"
        result = parse(text, tolerance=0)
        self.assertEqual(result.rows, [["a", "b"], ["1", "2"], ["3", "4"]])
        self.assertTrue(is_rule_line("---+---+---"))
        self.assertFalse(is_rule_line("a-b"))


class TestMerge(unittest.TestCase):
    COLSPAN_TEXT = (
        "部门    姓名  年龄\n"
        "技术部  张三  18\n"
        "技术部全体人员合影\n"      # 内容穿越两条空白带 → 跨 3 列
        "市场部  李四  20\n"
    )

    def test_colspan(self):
        result = parse(self.COLSPAN_TEXT, tolerance=1)
        self.assertEqual(result.errors, [])
        # 合并后：第 3 行是 1 个跨 3 列的单元格 + 2 个 None 占位
        self.assertEqual(result.rows[2], ["技术部全体人员合影", None, None])
        span = next(s for s in result.spans if s.colspan == 3)
        self.assertEqual((span.row, span.col), (2, 0))
        self.assertEqual(span.text, "技术部全体人员合影")
        # 每行有效列数一致
        for row in result.rows:
            self.assertEqual(len(row), 3)

    def test_rowspan(self):
        text = (
            "部门    姓名  年龄\n"
            "技术部  张三  18\n"
            "^^      李四  20\n"
            "^^      王五  22\n"
            "市场部  赵六  30\n"
        )
        result = parse(text, tolerance=0)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.rows[1][0], "技术部")
        self.assertIsNone(result.rows[2][0])   # 被跨行合并覆盖
        self.assertIsNone(result.rows[3][0])
        self.assertEqual(result.rows[4][0], "市场部")
        span = next(s for s in result.spans if s.rowspan == 3)
        self.assertEqual((span.row, span.col, span.text), (1, 0, "技术部"))

    def test_orphan_rowspan_marker_reported(self):
        # 第一行就是跨行标记：上方没有可合并单元格，必须报错并指出行号
        result = parse("^^   18\n张三  20\n", tolerance=0)
        self.assertEqual(len(result.errors), 1)
        err = result.errors[0]
        self.assertEqual(err.kind, "orphan_row_span")
        self.assertEqual(err.line_no, 1)

    def test_rowspan_marker_variants(self):
        for marker in ("↑", "〃", '"'):
            text = f"甲  1\n{marker}   2"
            result = parse(text, tolerance=0)
            self.assertIsNone(result.rows[1][0], marker)


class TestConsistency(unittest.TestCase):
    def test_validate_reports_row_and_deviation(self):
        rows = [["a", "b", "c"], ["1", "2"], ["x", "y", "z", "w"]]
        problems = validate(rows)
        self.assertEqual(len(problems), 2)
        self.assertIn("第 2 行", problems[0])
        self.assertIn("偏差 -1", problems[0])
        self.assertIn("第 3 行", problems[1])
        self.assertIn("偏差 +1", problems[1])

    def test_parse_result_rows_uniform(self):
        # 含跨行/跨列/空单元格的混合表格，结果每行列数必须一致
        text = (
            "部门    姓名  年龄  城市\n"
            "技术部  张三  18    北京\n"
            "^^      李四        上海\n"
            "技术部年度总结\n"
            "市场部  王五  22\n"
        )
        result = parse(text, tolerance=1)
        for i, row in enumerate(result.rows, 1):
            self.assertEqual(len(row), result.n_cols, f"第 {i} 行列数不一致")
        # 跨列行的文本只盖过前两条边界 → 跨 3 列，第 4 列是空单元格
        self.assertEqual(result.rows[3], ["技术部年度总结", None, None, ""])
        self.assertEqual(result.rows[4][3], "")  # 末尾空单元格


if __name__ == "__main__":
    unittest.main()
