"""tableparse 自测（仅标准库 unittest）。

运行：python3 -m unittest test_tableparse -v
"""

import unittest

from tableparse import (
    parse_table,
    infer_boundaries,
    validate_rows,
    display_width,
)


class TestBoundaryInference(unittest.TestCase):
    """列边界推断：依据全表空白带，而非固定列宽。"""

    def test_basic_two_columns(self):
        lines = ["aa   bb", "cc   dd"]
        bands = infer_boundaries(lines)
        self.assertEqual(bands, [(2, 5)])

    def test_single_column_no_boundary(self):
        # 单列：词间只有 1 个空格，小于 min_gap=2，不应产生边界
        table = parse_table("hello world\nfoo bar")
        self.assertEqual(table.n_columns, 1)
        self.assertEqual(table.rows, [["hello world"], ["foo bar"]])

    def test_boundary_follows_data_not_fixed_width(self):
        # 两表列宽不同，推断出的边界应各自贴合数据
        t1 = parse_table("a  1\nb  2")
        t2 = parse_table("aaaa  1\nbbbb  2")
        self.assertEqual(t1.columns[0], (0, 1))
        self.assertEqual(t2.columns[0], (0, 4))

    def test_fullwidth_occupies_two_columns(self):
        self.assertEqual(display_width("欧阳七七"), 8)
        self.assertEqual(display_width("ab"), 2)

    def test_fullwidth_alignment(self):
        text = (
            "姓名      城市\n"
            "欧阳七七  北京\n"
            "张三      上海"
        )
        table = parse_table(text)
        self.assertEqual(table.n_columns, 2)
        self.assertEqual(table.rows,
                         [["姓名", "城市"],
                          ["欧阳七七", "北京"],
                          ["张三", "上海"]])


class TestWhitespaceAndEmptyCells(unittest.TestCase):
    """不规则空白（空格数不一、制表符、全角空格）与空单元格。"""

    def test_irregular_whitespace(self):
        text = (
            "姓名   年龄\n"
            "张三   18\n"
            "李四\t\t20\n"
            "王五　 22"
        )
        table = parse_table(text, violation_rate=0.25)
        self.assertEqual(table.rows,
                         [["姓名", "年龄"],
                          ["张三", "18"],
                          ["李四", "20"],
                          ["王五", "22"]])

    def test_empty_cells(self):
        text = (
            "姓名   部门   备注\n"
            "张三   研发   全勤\n"
            "李四\n"
            "王五   市场   出差"
        )
        table = parse_table(text)
        self.assertEqual(table.rows[2], ["李四", "", ""])
        self.assertTrue(all(len(r) == 3 for r in table.rows))
        self.assertEqual(table.problems, [])

    def test_blank_lines_ignored(self):
        table = parse_table("a  1\n\n\nb  2\n")
        self.assertEqual(len(table.rows), 2)


class TestColspanMerge(unittest.TestCase):
    """跨列合并：内容越过推断出的边界空白带。"""

    TEXT = (
        "姓名    部门    职级     备注\n"
        "张三    研发    T5       全勤\n"
        "王五    市场    T4       同上\n"
        "李四    待分配           新入职\n"
        "赵六    市场    T6"
    )

    def test_colspan_merge(self):
        table = parse_table(self.TEXT)
        # “待分配”顶满部门列且职级列为空 => 第3行 部门+职级 跨列合并
        self.assertEqual(table.rows[3][1], "待分配")
        self.assertIsNone(table.rows[3][2])
        colspan = [m for m in table.merges if m.kind == "colspan"]
        self.assertEqual(len(colspan), 1)
        self.assertEqual(colspan[0].before, ["待分配", ""])
        self.assertEqual(colspan[0].after, "待分配")
        self.assertEqual(colspan[0].anchor, (3, 1))
        self.assertEqual(colspan[0].span, (1, 2))

    def test_header_not_merged(self):
        # 表头“职级”比数据宽，但不应当被误判为跨列合并
        table = parse_table(self.TEXT)
        self.assertEqual(table.rows[0], ["姓名", "部门", "职级", "备注"])

    def test_rowspan_marker(self):
        table = parse_table(self.TEXT)
        # “同上”并入上一行的“全勤”
        self.assertIsNone(table.rows[2][3])
        rowspan = [m for m in table.merges if m.kind == "rowspan"]
        self.assertEqual(len(rowspan), 1)
        self.assertEqual(rowspan[0].before, ["全勤", "同上"])
        self.assertEqual(rowspan[0].after, "全勤")
        self.assertEqual(rowspan[0].anchor, (1, 3))

    def test_grid_stays_rectangular(self):
        table = parse_table(self.TEXT)
        self.assertTrue(all(len(r) == 4 for r in table.rows))
        self.assertEqual(table.problems, [])


class TestCenteredRowspan(unittest.TestCase):
    """垂直居中踪迹的跨行合并（需显式开启）。"""

    TEXT = (
        "部门      姓名  季度\n"
        "          甲    Q1\n"
        "研发中心  乙    Q2\n"
        "          丙    Q3"
    )

    def test_centered_rowspan_enabled(self):
        table = parse_table(self.TEXT, centered_rowspan=True)
        self.assertEqual(table.rows[1][0], "研发中心")
        self.assertIsNone(table.rows[2][0])
        self.assertIsNone(table.rows[3][0])
        rowspan = [m for m in table.merges if m.kind == "rowspan"]
        self.assertEqual(len(rowspan), 1)
        self.assertEqual(rowspan[0].before, ["", "研发中心", ""])
        self.assertEqual(rowspan[0].after, "研发中心")
        self.assertEqual(rowspan[0].span, (3, 1))

    def test_centered_rowspan_disabled_by_default(self):
        table = parse_table(self.TEXT)
        self.assertEqual(table.rows[2][0], "研发中心")
        self.assertEqual(table.rows[1][0], "")
        self.assertEqual(table.merges, [])


class TestRowValidation(unittest.TestCase):
    """列数一致性校验：指出具体行与偏差量。"""

    def test_validate_rows_reports_deviation(self):
        rows = [["a", "b", "c"], ["x", "y"], ["p", "q", "r", "s"]]
        problems = validate_rows(rows)
        self.assertEqual(problems, [
            {"row": 1, "expected": 3, "actual": 2, "deviation": -1},
            {"row": 2, "expected": 3, "actual": 4, "deviation": 1},
        ])

    def test_validate_rows_consistent(self):
        self.assertEqual(validate_rows([["a"], ["b"]]), [])
        self.assertEqual(validate_rows([]), [])

    def test_parse_result_always_consistent(self):
        table = parse_table(TestColspanMerge.TEXT)
        lengths = {len(r) for r in table.rows}
        self.assertEqual(len(lengths), 1)
        self.assertEqual(table.problems, [])


class TestEdgeCases(unittest.TestCase):
    def test_empty_text(self):
        table = parse_table("")
        self.assertEqual(table.rows, [])
        self.assertEqual(table.n_columns, 0)

    def test_single_column_fullwidth(self):
        table = parse_table("第一行\n第二行\n第三行")
        self.assertEqual(table.n_columns, 1)
        self.assertEqual(len(table.rows), 3)

    def test_leading_indentation(self):
        text = "  姓名   年龄\n  张三   18"
        table = parse_table(text)
        self.assertEqual(table.rows[0], ["姓名", "年龄"])


if __name__ == "__main__":
    unittest.main()
