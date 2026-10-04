import json
import unittest

from formulas import Workbook, build_dependency_graph, evaluate_workbook, CellError
from formulas.cellref import format_cell, parse_cell
from tests.caselib import load_json, build_workbook


def n(sheet, addr):
    row, col = parse_cell(addr)
    return (sheet, row, col)


def addr(node):
    return "%s!%s" % (node[0], format_cell(node[1], node[2]))


class DependencyGraphTest(unittest.TestCase):
    def setUp(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("A1", 1)
        s.set("B1", "=A1+1")          # 依赖 A1
        s.set("C1", "=B1+A1")         # 依赖 B1, A1
        t = wb.add_sheet("T")
        t.set("D1", "=S!B1*2")        # 跨表依赖
        self.wb = wb
        self.g = build_dependency_graph(wb)

    def test_edges(self):
        self.assertEqual(self.g.dependencies(n("S", "B1")), {n("S", "A1")})
        self.assertEqual(
            self.g.dependencies(n("S", "C1")), {n("S", "B1"), n("S", "A1")}
        )
        self.assertEqual(self.g.dependencies(n("T", "D1")), {n("S", "B1")})
        self.assertEqual(self.g.dependents(n("S", "B1")),
                         {n("S", "C1"), n("T", "D1")})

    def test_range_expands_to_rectangle(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("D9", "=SUM(A1:B3)")
        g = build_dependency_graph(wb)
        deps = g.dependencies(n("S", "D9"))
        expected = {("S", r, c) for r in range(1, 4) for c in range(1, 3)}
        self.assertEqual(deps, expected)

    def test_topo_order_is_valid(self):
        order = self.g.evaluation_order()
        pos = {node: i for i, node in enumerate(order)}
        for src, dsts in self.g.edges.items():
            for dst in dsts:
                self.assertLess(pos[dst], pos[src], "%s 应在 %s 之前" % (dst, src))

    def test_self_reference(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("A1", "=A1+1")
        g = build_dependency_graph(wb)
        cycles = g.cycles()
        self.assertEqual(cycles, [[n("S", "A1")]])
        values, _ = evaluate_workbook(wb)
        self.assertEqual(values[n("S", "A1")], CellError("#CYCLE!"))

    def test_mutual_reference_two(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("A1", "=B1+1")
        s.set("B1", "=A1+1")
        g = build_dependency_graph(wb)
        cycles = g.cycles()
        self.assertEqual(sorted(cycles[0]), [n("S", "A1"), n("S", "B1")])

    def test_cycle_reports_all_nodes_three(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("A1", "=B1")
        s.set("B1", "=C1")
        s.set("C1", "=A1")
        s.set("D1", "=A1+1")              # 不在环上，但传播 #CYCLE!
        g = build_dependency_graph(wb)
        cycles = g.cycles()
        self.assertEqual(len(cycles), 1)
        self.assertEqual(
            sorted(cycles[0]), [n("S", "A1"), n("S", "B1"), n("S", "C1")]
        )
        values, _ = evaluate_workbook(wb)
        self.assertEqual(values[n("S", "D1")], CellError("#CYCLE!"))

    def test_multiple_disjoint_cycles(self):
        wb = Workbook()
        s = wb.add_sheet("S")
        s.set("A1", "=A1")               # 自环
        s.set("C1", "=D1")
        s.set("D1", "=C1")              # 双环
        s.set("E1", "=F1")
        s.set("F1", "=G1")
        s.set("G1", "=E1")              # 三环
        g = build_dependency_graph(wb)
        cycles = [sorted(c) for c in g.cycles()]
        sizes = sorted(len(c) for c in cycles)
        self.assertEqual(sizes, [1, 2, 3])

    def test_cross_sheet_cycle(self):
        wb = Workbook()
        wb.add_sheet("X").set("A1", "=Y!A1")
        wb.add_sheet("Y").set("A1", "=X!A1")
        g = build_dependency_graph(wb)
        cycles = g.cycles()
        self.assertEqual(sorted(cycles[0]), [n("X", "A1"), n("Y", "A1")])

    def test_cycle_samples_from_data(self):
        doc = load_json("cycle_samples.json")
        for sample in doc["samples"]:
            wb = build_workbook(sample)
            _, cycles = evaluate_workbook(wb)
            found = sorted(
                "%s!%s" % (node[0], format_cell(node[1], node[2]))
                for cyc in cycles for node in cyc
            )
            self.assertEqual(found, sample["cycle_nodes"], sample["name"])


if __name__ == "__main__":
    unittest.main()
