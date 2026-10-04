"""公式求值器。

语义约定（尽量贴近常见电子表格）：
- 空单元格在标量算术里按 0、在拼接里按空串；聚合函数会跳过空单元格。
- 错误值沿依赖链传播；IF 对未取到的分支短路不求值。
- 算术遇到无法转数字的文本 -> #VALUE!；除零 -> #DIV/0!；引用不存在的表 -> #REF!；
  未知函数/名称 -> #NAME?；非法数值（如 sqrt(-1)）-> #NUM!。
- 区域一律按矩形行优先（先行后列）顺序展开。
"""

import math

from .errors import (
    CellError,
    div_by_zero,
    value_error,
    name_error,
    ref_error,
    num_error,
    cycle_error,
)
from .graph import build_dependency_graph
from .parser import parse_formula


class _Err(Exception):
    def __init__(self, cell_error):
        self.cell_error = cell_error


def _raise(err):
    raise _Err(err)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def to_number(v):
    """标量转数字；文本不是数字时抛 #VALUE!，空值为 0。"""
    if isinstance(v, CellError):
        _raise(v)
    if isinstance(v, bool):
        return 1 if v else 0
    if is_number(v):
        return v
    if v is None:
        return 0
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0
        try:
            if s.upper() == "TRUE":
                return 1
            if s.upper() == "FALSE":
                return 0
            return float(s)
        except ValueError:
            _raise(value_error())
    _raise(value_error())


def to_text(v):
    if isinstance(v, CellError):
        _raise(v)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if is_number(v):
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return ("%.15g" % v)
    return str(v)


def to_bool(v):
    if isinstance(v, CellError):
        _raise(v)
    if isinstance(v, bool):
        return v
    if is_number(v):
        return v != 0
    if v is None:
        return False
    if isinstance(v, str):
        s = v.strip().upper()
        if s == "TRUE":
            return True
        if s in ("FALSE", ""):
            return False
    _raise(value_error())


_CMP_RANK = {"num": 0, "text": 1}


def _cmp_key(v):
    if isinstance(v, CellError):
        _raise(v)
    if v is None:
        return (0, 0)
    if isinstance(v, bool):
        return (0, 1 if v else 0)
    if is_number(v):
        return (0, v)
    if isinstance(v, str):
        return (1, v)
    _raise(value_error())


def _compare(op, a, b):
    ka, kb = _cmp_key(a), _cmp_key(b)
    if op == "=":
        return ka == kb
    if op == "<>":
        return ka != kb
    if op == "<":
        return ka < kb
    if op == "<=":
        return ka <= kb
    if op == ">":
        return ka > kb
    return ka >= kb


class Evaluator:
    def __init__(self, workbook, graph=None, env=None):
        self.wb = workbook
        self.graph = graph if graph is not None else build_dependency_graph(workbook)
        self.env = env or {}        # node -> 已算出的值（可预先放入）
        self.stats = {"evals": 0}

    # ---- 值读取 ----
    def read_raw(self, sheet, row, col):
        """读单元格原始输入（常量或 None）。公式单元格应已在 env 里。"""
        try:
            sheet_obj = self.wb.sheet(sheet)
        except KeyError:
            _raise(ref_error())
        if (sheet, row, col) in self.env:
            return self.env[(sheet, row, col)]
        cell = sheet_obj.get(row, col)
        if cell is None:
            return None
        if cell.is_formula:
            # 拓扑序之外的公式引用（不应该出现，除非漏图）：现算
            return self.eval_cell((sheet, row, col))
        return cell.raw

    def _range_values(self, rng, current_sheet):
        vals = []
        for (sheet, row, col) in rng.addresses(current_sheet):
            vals.append(self.read_raw(sheet, row, col))
        return vals

    # ---- AST 求值 ----
    def eval_ast(self, node, current_sheet):
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "str":
            return node[1]
        if kind == "bool":
            return node[1]
        if kind == "error":
            return CellError(node[1])
        if kind == "ref":
            sheet, row, col = node[1].resolve(current_sheet)
            return self.read_raw(sheet, row, col)
        if kind == "range":
            # 区域不能直接当标量
            vals = self._range_values(node[1], current_sheet)
            return vals
        if kind == "unary":
            v = to_number(self.eval_ast(node[2], current_sheet))
            return -v if node[1] == "-" else v
        if kind == "bin":
            return self._eval_bin(node, current_sheet)
        if kind == "call":
            return self._eval_call(node, current_sheet)
        raise RuntimeError("未知 AST 节点 %r" % (node,))

    def _eval_bin(self, node, current_sheet):
        _, op, left_n, right_n = node
        if op == "&":
            return to_text(self.eval_ast(left_n, current_sheet)) + to_text(
                self.eval_ast(right_n, current_sheet)
            )
        if op in ("=", "<>", "<", "<=", ">", ">="):
            return _compare(
                op,
                self.eval_ast(left_n, current_sheet),
                self.eval_ast(right_n, current_sheet),
            )
        a = to_number(self.eval_ast(left_n, current_sheet))
        b = to_number(self.eval_ast(right_n, current_sheet))
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                _raise(div_by_zero())
            return a / b
        if op == "^":
            if a < 0 and not float(b).is_integer():
                _raise(num_error())
            return math.pow(a, b)
        raise RuntimeError("未知运算符 %r" % op)

    def _flatten_args(self, args, current_sheet):
        """把参数展开为标量列表（区域按矩形行优先展开）。"""
        out = []
        for arg in args:
            if arg[0] == "range":
                out.extend(self._range_values(arg[1], current_sheet))
            else:
                out.append(self.eval_ast(arg, current_sheet))
        return out

    def _numeric_list(self, values):
        """聚合用数字列表：跳过空单元格与文本，错误传播，布尔按 1/0（Excel 区域里
        通常忽略布尔，这里对显式逻辑值仍计数，空与文本跳过）。"""
        nums = []
        for v in values:
            if v is None:
                continue
            if isinstance(v, CellError):
                _raise(v)
            if isinstance(v, bool):
                nums.append(1 if v else 0)
            elif is_number(v):
                nums.append(v)
            elif isinstance(v, str) and v.strip() != "":
                continue  # 聚合跳过文本
        return nums

    def _eval_call(self, node, current_sheet):
        _, fname, args = node
        if fname == "IF":
            if len(args) not in (2, 3):
                _raise(value_error())
            cond = to_bool(self.eval_ast(args[0], current_sheet))
            chosen = args[1] if cond else (args[2] if len(args) == 3 else ("bool", False))
            return self.eval_ast(chosen, current_sheet)

        if fname in ("SUM", "AVERAGE", "AVERAGEA", "MIN", "MAX", "PRODUCT", "COUNT", "COUNTA"):
            vals = self._flatten_args(args, current_sheet)
            nums = self._numeric_list(vals)
            if fname == "SUM":
                return math.fsum(nums)
            if fname == "PRODUCT":
                acc = 1.0
                for x in nums:
                    acc *= x
                return acc
            if fname == "COUNT":
                return float(len(nums))
            if fname == "COUNTA":
                return float(sum(1 for v in vals if v is not None and v != ""))
            if fname == "MIN":
                return min(nums) if nums else 0.0
            if fname == "MAX":
                return max(nums) if nums else 0.0
            if not nums:
                _raise(div_by_zero())
            avg = math.fsum(nums) / len(nums)
            return avg

        if fname in ("AND", "OR"):
            vals = self._flatten_args(args, current_sheet)
            logicals = []
            for v in vals:
                if v is None:
                    continue
                if isinstance(v, CellError):
                    _raise(v)
                if isinstance(v, str):
                    if v.strip() == "":
                        continue
                    _raise(value_error())
                logicals.append(bool(v) if isinstance(v, bool) else v != 0)
            if not logicals:
                _raise(value_error())
            return all(logicals) if fname == "AND" else any(logicals)

        scalars = []
        for arg in args:
            if arg[0] == "range":
                _raise(value_error())
            scalars.append(self.eval_ast(arg, current_sheet))

        if fname == "ABS":
            return abs(to_number(scalars[0])) if len(scalars) == 1 else _raise(value_error())
        if fname == "SQRT":
            x = to_number(scalars[0])
            if x < 0:
                _raise(num_error())
            return math.sqrt(x)
        if fname == "ROUND":
            x = to_number(scalars[0])
            d = int(to_number(scalars[1]))
            scale = 10.0 ** d
            if x >= 0:
                return math.floor(x * scale + 0.5) / scale
            return math.ceil(x * scale - 0.5) / scale
        if fname == "INT":
            return float(math.floor(to_number(scalars[0])))
        if fname == "MOD":
            a, b = to_number(scalars[0]), to_number(scalars[1])
            if b == 0:
                _raise(div_by_zero())
            return a - b * math.floor(a / b)
        if fname == "POWER":
            a, b = to_number(scalars[0]), to_number(scalars[1])
            if a < 0 and not float(b).is_integer():
                _raise(num_error())
            return math.pow(a, b)
        _raise(name_error())

    # ---- 单元格 / 整本求值 ----
    def eval_cell(self, node):
        if node in self.env:
            return self.env[node]
        sheet, row, col = node
        ast = self.graph.formulas.get(node)
        if ast is None:
            return self.read_raw(sheet, row, col)
        self.stats["evals"] += 1
        try:
            value = self.eval_ast(ast, sheet)
        except _Err as e:
            value = e.cell_error
        self.env[node] = value
        return value


def evaluate_workbook(workbook):
    """对整本工作簿求值。

    返回 (values, cycles)：
    - values: {(sheet,row,col): 值}，含每个公式单元格；环上单元格为 #CYCLE!
      （依赖它们的单元格传播 #CYCLE!）。
    - cycles: [[节点, ...], ...]，每个循环列出环上全部单元格。
    """
    graph = build_dependency_graph(workbook)
    cycles = graph.cycles()
    evaluator = Evaluator(workbook, graph)
    cyclic = graph.cyclic_nodes()
    for n in cyclic:
        evaluator.env[n] = cycle_error()
    for n in graph.evaluation_order():
        if n in graph.formulas:
            evaluator.eval_cell(n)
    return evaluator.env, cycles
