"""tableparse - 纯文本对齐表格解析库（仅使用 Python 标准库）。

能力概览：
1. 依据“全表空白带”推断列边界，而不是按固定列宽硬切；
2. 识别跨列合并（内容越过边界空白带）与跨行合并（“同上”类标记 /
   垂直居中两种踪迹），并把内容归并到正确的锚点单元格；
3. 输出列数一致的矩形网格（被合并覆盖的单元格为 None），
   并提供 validate_rows 对任意行列表做列数一致性校验。

显示宽度约定：全角字符（East Asian Width 为 W/F）占 2 列，
组合字符占 0 列，其余占 1 列；所有制表符与全角空格在归一化时
展开为 2 个普通空格。
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass

__all__ = [
    "Merge",
    "Table",
    "parse_table",
    "infer_boundaries",
    "validate_rows",
    "char_width",
    "display_width",
    "format_grid",
    "DEFAULT_ROWSPAN_MARKERS",
]

DEFAULT_ROWSPAN_MARKERS = ("同上", "↑", "↑↑", "″", "〃", "^")


# ---------------------------------------------------------------------------
# 显示宽度
# ---------------------------------------------------------------------------

def char_width(ch: str) -> int:
    """单个字符的显示宽度：全角 2，组合字符 0，其余 1。"""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def _normalize(line: str) -> str:
    # 全角空格展开为 2 个半角空格以保持显示宽度；制表符展开为 2 个空格，
    # 保证其达到默认 min_gap=2 的边界宽度。行尾空白无信息，直接去掉。
    return line.replace("　", "  ").replace("\t", "  ").rstrip()


def _to_display_cells(line: str) -> list[str]:
    """把一行展开成“显示列”数组：每个显示列一个槽位。

    全角字符占两个槽位（第二个槽位为空串占位），这样后续可以
    直接按显示列下标切片，天然支持全角对齐的表格。
    """
    cells: list[str] = []
    for ch in line:
        w = char_width(ch)
        if w == 0:
            if cells:
                cells[-1] += ch
            continue
        cells.append(ch)
        if w == 2:
            cells.append("")
    return cells


def _slice_cells(cells: list[str], a: int, b: int) -> str:
    if a >= len(cells):
        return ""
    return "".join(cells[a:min(b, len(cells))]).strip()


# ---------------------------------------------------------------------------
# 列边界推断
# ---------------------------------------------------------------------------

def _infer(cell_lines: list[list[str]], min_gap: int, violation_rate: float):
    """返回 (边界空白带列表, 表格总显示宽度)。"""
    width = max((len(c) for c in cell_lines), default=0)
    n = len(cell_lines)
    # occ[c] = 在显示列 c 上有非空白字符的行数
    occ = [0] * width
    for cells in cell_lines:
        for i, ch in enumerate(cells):
            if ch != " ":  # 全角字符的占位槽位（""）同样视为占用
                occ[i] += 1
    # 允许少量行“越界”（跨列合并的踪迹），越界行数不超过
    # floor(n * violation_rate) 的显示列仍视为候选分隔列。
    max_violators = int(n * violation_rate)
    bands: list[tuple[int, int]] = []
    i = 0
    while i < width:
        if occ[i] <= max_violators:
            j = i
            while j < width and occ[j] <= max_violators:
                j += 1
            if j - i >= min_gap:
                # 候选带内若存在“所有行都完全空白”的核心段，就把边界
                # 收紧到最长的核心段，避免边界吞掉某行较宽的正常文字
                # （如表头“职级”比数据“T5”宽、或“欧阳七七”比“张三”
                # 宽的情形）；越界文字留在核心段外侧，作为跨列合并的
                # 踪迹在解析阶段识别。
                # 只有存在全空核心段才认定为边界：全空列都没有的位置
                # 不能构成可靠切分线（例如少数行写满到行尾形成的尾部
                # 幻影带），直接舍弃。
                cores = []
                k = i
                while k < j:
                    if occ[k] == 0:
                        h = k
                        while h < j and occ[h] == 0:
                            h += 1
                        cores.append((k, h))
                        k = h
                    else:
                        k += 1
                if cores:
                    bands.append(max(cores, key=lambda p: p[1] - p[0]))
            i = j
        else:
            i += 1
    return bands, width


def infer_boundaries(lines, min_gap: int = 2, violation_rate: float = 0.2):
    """推断列边界，返回边界空白带列表 [(start, end), ...]（显示列坐标）。

    推断依据：
    - 真正的列间隙应当在（几乎）所有行上都是空白。对每一个显示列
      统计“有非空白字符的行数”，不超过 floor(n * violation_rate)
      的列视为可分隔列；
    - 连续 >= min_gap 个可分隔列构成候选边界带；若带内存在所有行都
      完全空白的核心段，边界收紧到最长核心段（防止吞掉较宽的正常
      单元格文字）；核心段外侧的越界文字由 parse_table 识别为跨列
      合并踪迹；
    - 全角字符按 2 个显示列计算，保证中文对齐的表格也能正确切分。
    """
    normed = [_normalize(l) for l in lines if l.strip()]
    if not normed:
        return []
    cell_lines = [_to_display_cells(l) for l in normed]
    bands, _ = _infer(cell_lines, min_gap, violation_rate)
    return bands


def _layout(bands: list[tuple[int, int]], width: int):
    """由边界带生成列布局：[(start, end, 右侧边界带下标或 None), ...]。"""
    cols = []
    prev = 0
    for bi, (s, e) in enumerate(bands):
        if s > prev:
            cols.append((prev, s, bi))
        prev = e
    if prev < width:
        cols.append((prev, width, None))
    return cols


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class Merge:
    """一次单元格合并的记录（合并前后对照）。"""
    kind: str            # 'colspan'（跨列）或 'rowspan'（跨行）
    anchor: tuple        # 合并后内容所在单元格 (row, col)
    span: tuple          # 合并范围 (行数, 列数)
    before: list         # 合并前涉及的单元格原文
    after: str           # 合并后的单元格内容


@dataclass
class Table:
    columns: list        # 每列的显示列区间 (start, end)
    bands: list          # 推断出的边界空白带
    rows: list           # 合并后的矩形网格；被合并覆盖的单元格为 None
    raw_rows: list       # 合并前的网格（仅切分、未归并）
    merges: list         # Merge 记录列表（合并前后对照数据）
    problems: list       # validate_rows 的校验结果（正常应为空）

    @property
    def n_columns(self) -> int:
        return len(self.columns)


# ---------------------------------------------------------------------------
# 行列表校验
# ---------------------------------------------------------------------------

def validate_rows(rows) -> list[dict]:
    """校验每行列数是否一致，返回不一致行的报告。

    期望列数取众数；每个问题项包含行号、期望列数、实际列数与偏差量。
    """
    if not rows:
        return []
    expected = Counter(len(r) for r in rows).most_common(1)[0][0]
    return [
        {"row": i, "expected": expected, "actual": len(r),
         "deviation": len(r) - expected}
        for i, r in enumerate(rows)
        if len(r) != expected
    ]


# ---------------------------------------------------------------------------
# 主解析流程
# ---------------------------------------------------------------------------

def parse_table(text: str,
                min_gap: int = 2,
                violation_rate: float = 0.2,
                rowspan_markers=DEFAULT_ROWSPAN_MARKERS,
                centered_rowspan: bool = False) -> Table:
    """解析纯文本对齐表格。

    参数：
    - min_gap: 构成列边界的最小空白带宽度（显示列）；
    - violation_rate: 边界带内容许的越界行比例（识别跨列合并）；
    - rowspan_markers: 视为“与上一行合并”的标记文本；
    - centered_rowspan: 是否启用“垂直居中值”跨行推断（默认关闭，
      因为该踪迹与真实的空单元格难以区分，需调用方确认）。
    """
    lines = [_normalize(l) for l in text.splitlines() if l.strip()]
    if not lines:
        return Table(columns=[], bands=[], rows=[], raw_rows=[],
                     merges=[], problems=[])

    cell_lines = [_to_display_cells(l) for l in lines]
    bands, width = _infer(cell_lines, min_gap, violation_rate)
    layout = _layout(bands, width)
    columns = [(a, b) for a, b, _ in layout]
    ncols = len(columns)
    left_col_of_band = {bi: ci for ci, (_, _, bi) in enumerate(layout)
                        if bi is not None}

    raw_rows: list[list] = []
    rows: list[list] = []
    merges: list[Merge] = []

    for r, cells in enumerate(cell_lines):
        # 1) 按列区间切分。
        row = [_slice_cells(cells, a, b) for a, b, _ in layout]

        # 2) 识别被越界的边界带（跨列合并踪迹）。
        #    强信号：该行在边界带内部有文字；
        #    弱信号：左侧文字顶到边界边缘（左列没有任何留白）且右侧
        #    单元格为空——说明文字在视觉上延续进了相邻的空列。
        #    注意右侧不能对称使用：左对齐表格里右列文字本来就贴着
        #    列首，无法据此区分“空单元格”与“合并”。
        violated = set()
        for bi, (s, e) in enumerate(bands):
            if _slice_cells(cells, s, e):
                violated.add(bi)
                continue
            c0 = left_col_of_band.get(bi)
            if c0 is None:
                continue
            left_touches = 0 < s < len(cells) and cells[s - 1] != " "
            right_empty = c0 + 1 >= len(row) or not row[c0 + 1]
            if left_touches and right_empty:
                violated.add(bi)

        # 越界带内部的文字并入其左侧列（视觉上属于同一合并单元格）。
        for ci, (a, b, right_band) in enumerate(layout):
            if right_band is not None and right_band in violated:
                crossed = _slice_cells(cells, *bands[right_band])
                row[ci] = (row[ci] + " " + crossed).strip()
        raw_rows.append(list(row))

        # 3) 跨列合并：连续的越界空白带 => 其夹着的列属于同一单元格。
        for group in _group_consecutive(sorted(violated)):
            c0 = left_col_of_band[group[0]]
            c1 = min(left_col_of_band[group[-1]] + 1, ncols - 1)
            if c1 <= c0:
                continue
            cols = list(range(c0, c1 + 1))
            before = [row[c] for c in cols]
            after = " ".join(x for x in before if x)
            for c in cols[1:]:
                row[c] = None
            row[cols[0]] = after
            merges.append(Merge("colspan", (r, cols[0]),
                                (1, len(cols)), before, after))
        rows.append(row)

    # 4) 跨行合并：显式标记（同上 / ↑ / ″ ...）。
    if rowspan_markers:
        _merge_rowspan_markers(rows, ncols, rowspan_markers, merges)

    # 5) 跨行合并：垂直居中值（可选）。
    if centered_rowspan:
        _merge_centered_rowspan(rows, ncols, merges)

    return Table(columns=columns, bands=bands, rows=rows,
                 raw_rows=raw_rows, merges=merges,
                 problems=validate_rows(rows))


def _group_consecutive(indices: list[int]):
    """把已排序整数列表分成连续段。"""
    if not indices:
        return
    group = [indices[0]]
    for x in indices[1:]:
        if x == group[-1] + 1:
            group.append(x)
        else:
            yield group
            group = [x]
    yield group


def _merge_rowspan_markers(rows, ncols, markers, merges):
    """显式跨行标记：单元格文本命中 markers 时并入上方最近的取值单元格。"""
    nrows = len(rows)
    for c in range(ncols):
        anchor = None
        pending_rows: list[int] = []
        pending_texts: list[str] = []

        def flush():
            if anchor is not None and pending_rows:
                before = [rows[anchor][c]] + list(pending_texts)
                merges.append(Merge("rowspan", (anchor, c),
                                    (len(pending_rows) + 1, 1),
                                    before, rows[anchor][c]))

        for r in range(nrows):
            v = rows[r][c]
            if v is not None and v in markers:
                if anchor is not None:
                    pending_rows.append(r)
                    pending_texts.append(v)
                    rows[r][c] = None
            else:
                flush()
                pending_rows = []
                pending_texts = []
                if v not in (None, ""):
                    anchor = r
        flush()


def _merge_centered_rowspan(rows, ncols, merges):
    """垂直居中踪迹：某列一段连续行中只有一个非空单元格，且它不在
    段首（上方至少有一个空格），视为跨行合并，值上移到段首。"""
    nrows = len(rows)
    for c in range(ncols):
        m = 0
        while m < nrows:
            v = rows[m][c]
            if v in (None, ""):
                m += 1
                continue
            a = m
            while a - 1 >= 0 and rows[a - 1][c] == "":
                a -= 1
            b = m
            while b + 1 < nrows and rows[b + 1][c] == "":
                b += 1
            if b > a and m > a:
                before = [rows[r][c] for r in range(a, b + 1)]
                rows[a][c] = v
                for r in range(a + 1, b + 1):
                    rows[r][c] = None
                merges.append(Merge("rowspan", (a, c), (b - a + 1, 1),
                                    before, v))
            m = b + 1


# ---------------------------------------------------------------------------
# 展示辅助
# ---------------------------------------------------------------------------

def format_grid(rows, none_text: str = "──") -> str:
    """把网格格式化为等宽对齐文本（全角感知），None 显示为 none_text。"""
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    widths = [0] * ncols
    for r in rows:
        for i in range(ncols):
            v = r[i] if i < len(r) else ""
            v = none_text if v is None else str(v)
            widths[i] = max(widths[i], display_width(v))
    out = []
    for r in rows:
        parts = []
        for i in range(ncols):
            v = r[i] if i < len(r) else ""
            v = none_text if v is None else str(v)
            parts.append(v + " " * (widths[i] - display_width(v)))
        out.append(" | ".join(parts).rstrip())
    return "\n".join(out)
