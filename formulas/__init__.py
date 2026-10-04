"""表格公式求值与依赖分析库（仅标准库）。

公开接口：
- Workbook / Sheet：数据模型
- DependencyGraph, build_dependency_graph：依赖有向图、循环检测、拓扑序
- Evaluator, evaluate_workbook：公式求值
- parse_formula, collect_refs：语法解析与引用收集
- CellRef, Range：地址与矩形区域（行优先遍历）
- CellError：错误值
"""

from .cellref import CellRef, Range, col_to_num, num_to_col, parse_cell, format_cell
from .errors import CellError, ERROR_LITERALS
from .graph import DependencyGraph, build_dependency_graph
from .model import Workbook, Sheet, Cell
from .parser import ParseError, parse_formula, collect_refs
from .evaluator import Evaluator, evaluate_workbook

__all__ = [
    "Workbook",
    "Sheet",
    "Cell",
    "DependencyGraph",
    "build_dependency_graph",
    "Evaluator",
    "evaluate_workbook",
    "ParseError",
    "parse_formula",
    "collect_refs",
    "CellRef",
    "Range",
    "CellError",
    "ERROR_LITERALS",
    "col_to_num",
    "num_to_col",
    "parse_cell",
    "format_cell",
]
