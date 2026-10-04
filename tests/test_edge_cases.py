import unittest

from formulas import Workbook, evaluate_workbook, CellError, Evaluator, build_dependency_graph
from formulas.cellref import parse_cell, Range, format_cell
from tests.caselib import run_eval_case, load_json


def n(sheet, addr):
    r, c = parse_cell(addr)
    return (sheet, r, c)


class RangeTraversalTest(unittest.TestCase):
    def test_rectangle_row_major_order(self):
        rng = Range.parse("B2:C3")
        cells = list(rng.addresses("S"))
        coords = [(r, c) for _, r, c in cells]
        self.assertEqual(coords, [(2, 2), (2, 3), (3, 2), (3, 3)])

    def test_swapped_corners_normalized(self):
        self.assertEqual(Range.parse("C3:B2"), Range.parse("B2:C3"))


class EdgeCaseTest(unittest.TestCase):
    def _one(self, formula, value=None):
        wb = Workbook()
        s = wb.add_sheet("S")
        if value is not None:
            s.set("A1", value)
        s.set("Z1", formula)
        values, _ = evaluate_workbook(wb)
        return values[n("S", "Z1")]

    def test_div_by_zero_variants(self):
        self.assertEqual(self._one("=0/0"), CellError("#DIV/0!"))
        self.assertEqual(self._one("=A1/0"), CellError("#DIV/0!"))  # 空格=0
        self.assertEqual(self._one("=AVERAGE(B1:B5)"), CellError("#DIV/0!"))  # 全空

    def test_numeric_text_in_scalar_vs_range(self):
        self.assertEqual(self._one("=A1+1", "5"), 6)       # 数字文本标量强转
        self.assertEqual(self._one('=A1+1', "abc"), CellError("#VALUE!"))
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("A1", 1); s.set("A2", "x"); s.set("A3", 2)
        s.set("B1", "=SUM(A1:A3)")          # 区域聚合跳过文本
        s.set("B2", "=A2+1")                # 标量算术 #VALUE!
        values, _ = evaluate_workbook(wb)
        self.assertEqual(values[n("S", "B1")], 3)
        self.assertEqual(values[n("S", "B2")], CellError("#VALUE!"))

    def test_empty_vs_blank_string(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("A2", "")
        s.set("B1", "=A1+1")    # 真空
        s.set("B2", "=A2+1")    # 空字符串
        s.set("C1", "=COUNTA(A1:A2)")
        values, _ = evaluate_workbook(wb)
        self.assertEqual(values[n("S", "B1")], 1)
        self.assertEqual(values[n("S", "B2")], 1)
        self.assertEqual(values[n("S", "C1")], 0)  # 空串与真空都不计数

    def test_quoted_sheet_name_with_space(self):
        wb = Workbook()
        wb.add_sheet("Sales Q1").set("B2", 21)
        wb.add_sheet("S").set("A1", "='Sales Q1'!B2")
        values, _ = evaluate_workbook(wb)
        self.assertEqual(values[n("S", "A1")], 21)

    def test_float_precision_fsum(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        for r in range(1, 11):
            s.set("A%d" % r, 0.1)
        s.set("B1", "=SUM(A1:A10)")
        values, _ = evaluate_workbook(wb)
        self.assertAlmostEqual(values[n("S", "B1")], 1.0, places=12)

    def test_percent_and_unary(self):
        self.assertEqual(self._one("=-50%"), -0.5)
        self.assertEqual(self._one("=200%%"), 0.02)
        self.assertAlmostEqual(self._one("=10%+20%"), 0.3)

    def test_power_edge(self):
        self.assertEqual(self._one("=(-8)^(1/3)"), CellError("#NUM!"))
        self.assertEqual(self._one("=2^10"), 1024)

    def test_bool_in_formula(self):
        self.assertEqual(self._one("=TRUE+1"), 2)
        self.assertEqual(self._one("=FALSE*5"), 0)
        self.assertEqual(self._one("=IF(TRUE,1,2)"), 1)

    def test_data_driven_edge_cases(self):
        doc = load_json("edge_cases.json")
        for case in doc["cases"]:
            results, _ = run_eval_case(case)
            for key, (got, want) in results.items():
                if isinstance(want, float):
                    self.assertAlmostEqual(got, want, places=10,
                                           msg="%s: %s" % (case["name"], key))
                else:
                    self.assertEqual(got, want,
                                     "%s: %s got=%r want=%r" % (case["name"], key, got, want))


if __name__ == "__main__":
    unittest.main()
