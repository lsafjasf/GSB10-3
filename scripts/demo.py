#!/usr/bin/env python3
"""库用法演示：建模 -> 依赖图 -> 环检测 -> 拓扑求值。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from formulas import Workbook, build_dependency_graph, evaluate_workbook
from formulas.cellref import format_cell


def show(node):
    return "%s!%s" % (node[0], format_cell(node[1], node[2]))


wb = Workbook()
s = wb.add_sheet("Sheet1")
s.set("A1", 10)
s.set("A2", 20)
s.set("A3", 30)
s.set("B1", "=SUM(A1:A3)")            # 60
s.set("B2", "=B1*2+Sheet2!C1")        # 跨表
s.set("B3", "=IF(B1>50, \"big\", \"small\")")
s.set("D1", "=D2+1")                  # ┐ 互相引用
s.set("D2", "=D1+1")                  # ┘
s.set("E1", "=E1")                    # 自引用
wb.add_sheet("Sheet2").set("C1", 5)

graph = build_dependency_graph(wb)
print("== 依赖边（公式格 -> 它依赖的格子）")
for node in sorted(graph.edges):
    deps = ", ".join(show(d) for d in sorted(graph.dependencies(node)))
    print("  %-12s -> %s" % (show(node), deps))

print("\n== 循环引用（环上全部单元格）")
for cyc in graph.cycles():
    print("  环:", ", ".join(show(n) for n in cyc))

print("\n== 拓扑求值顺序")
print(" ", " -> ".join(show(n) for n in graph.evaluation_order()))

values, cycles = evaluate_workbook(wb)
print("\n== 求值结果")
for node in sorted(values):
    print("  %-12s = %s" % (show(node), values[node]))
