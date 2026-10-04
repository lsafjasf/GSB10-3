import unittest

from formulas import Workbook, evaluate_workbook, CellError, Evaluator, parse_formula
from formulas.cellref import parse_cell


class EvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.wb = Workbook()
        self.s = self.wb.add_sheet("S")
        self.s.set("A1", 10)
        self.s.set("A2", 3)
        self.s.set("A3", -5)
        self.s.set("A4", 2.5)

    def val(self, formula, cell="Z1"):
        self.s.set(cell, formula)
        values, _ = evaluate_workbook(self.wb)
        return values[("S", *parse_cell(cell))]

    def test_arithmetic_precedence_and_parens(self):
        self.assertEqual(self.val("=1+2*3"), 7)
        self.assertEqual(self.val("=(1+2)*3"), 9)
        self.assertEqual(self.val("=10-2-3"), 5)
        self.assertEqual(self.val("=100/5/4"), 5)
        self.assertEqual(self.val("=2^3^2"), 64)          # 左结合
        self.assertEqual(self.val("=2*-(3+1)"), -8)
        self.assertEqual(self.val("=50%"), 0.5)
        self.assertEqual(self.val("=(200+100)%"), 3.0)

    def test_cell_refs_and_aggregate_functions(self):
        self.assertEqual(self.val("=A1+A2"), 13)
        self.assertEqual(self.val("=SUM(A1:A4)"), 10.5)
        self.assertEqual(self.val("=AVERAGE(A1:A2)"), 6.5)
        self.assertEqual(self.val("=MIN(A1:A3)"), -5)
        self.assertEqual(self.val("=MAX(A1:A4)"), 10)
        self.assertEqual(self.val("=COUNT(A1:A4)"), 4)
        self.assertEqual(self.val("=PRODUCT(A1:A2)"), 30)
        self.assertEqual(self.val("=SUM(A1:A2, 100, A4)"), 115.5)

    def test_comparison_and_concat(self):
        self.assertIs(self.val("=A1>A2"), True)
        self.assertIs(self.val("=A3>=0"), False)
        self.assertIs(self.val("=1<>2"), True)
        self.assertEqual(self.val('="n="&A1'), "n=10")
        self.assertEqual(self.val('="a"&""&1'), "a1")

    def test_functions_extended(self):
        self.assertEqual(self.val("=ABS(A3)"), 5)
        self.assertEqual(self.val("=SQRT(16)"), 4)
        self.assertEqual(self.val("=ROUND(3.14159,2)"), 3.14)
        self.assertEqual(self.val("=MOD(10,3)"), 1)
        self.assertEqual(self.val("=MOD(-1,3)"), 2)
        self.assertEqual(self.val("=POWER(2,10)"), 1024)
        self.assertEqual(self.val("=IF(A1>5,1,2)"), 1)
        self.assertIs(self.val("=AND(1,2>1)"), True)
        self.assertIs(self.val("=OR(0,2<1)"), False)

    def test_empty_cell_semantics(self):
        self.assertEqual(self.val("=B9+1"), 1)            # 空单元格做算术为 0
        self.assertEqual(self.val('=""&B9'), "")         # 空单元格拼接为空串
        self.assertEqual(self.val("=SUM(A1:A100)"), 10.5)  # 空区域格被跳过
        self.assertEqual(self.val("=COUNT(A1:A100)"), 4)
        self.assertEqual(self.val("=COUNTA(A1:A100)"), 4)
        self.assertEqual(self.val("=MIN(B1:B5)"), 0)

    def test_error_propagation(self):
        self.assertEqual(self.val("=1/0"), CellError("#DIV/0!"))
        self.assertEqual(self.val("=A1/0 + 9"), CellError("#DIV/0!"))
        self.assertEqual(self.val('=1+"abc"'), CellError("#VALUE!"))
        self.assertEqual(self.val("=SQRT(-1)"), CellError("#NUM!"))
        self.assertEqual(self.val("=NOSUCHFN(1)"), CellError("#NAME?"))
        self.s.set("E1", "=E2*2")
        self.s.set("E2", "=1/0")
        values, _ = evaluate_workbook(self.wb)
        self.assertEqual(values[("S", *parse_cell("E1"))], CellError("#DIV/0!"))

    def test_if_short_circuit(self):
        # 未取分支里的错误不应触发
        self.assertEqual(self.val('=IF(1>0, 7, 1/0)'), 7)
        self.assertEqual(self.val('=IF(1<0, 1/0, 8)'), 8)
        # 条件本身是错误则传播
        self.assertEqual(self.val('=IF(1/0, 1, 2)'), CellError("#DIV/0!"))

    def test_error_literal_in_cell(self):
        self.s.set("G1", "=#N/A")
        self.assertEqual(self.val("=G1+1", "G2"), CellError("#N/A"))

    def test_cross_sheet_reference(self):
        t = self.wb.add_sheet("Data 2")
        t.set("B2", 40)
        self.assertEqual(self.val("='Data 2'!B2*2", "H1"), 80)
        self.assertEqual(self.val("=SUM('Data 2'!B1:B2)", "H2"), 40)
        self.assertEqual(self.val("=Missing!A1", "H3"), CellError("#REF!"))

    def test_evaluation_order_respects_deps(self):
        self.s.set("K1", "=K2+K3")
        self.s.set("K2", "=K3*2")
        self.s.set("K3", 5)
        values, _ = evaluate_workbook(self.wb)
        # values 只含公式单元格；K3 是常量
        self.assertEqual(values[("S", *parse_cell("K2"))], 10)
        self.assertEqual(values[("S", *parse_cell("K1"))], 15)

    def test_parser_rejects_bare_range(self):
        from formulas import ParseError
        with self.assertRaises(ParseError):
            parse_formula("=A1:B2+1")


if __name__ == "__main__":
    unittest.main()
