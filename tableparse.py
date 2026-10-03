"""tableparse — 纯文本对齐表格解析库（仅使用 Python 标准库）。

核心流程：
1. 显示宽度归一化：全角字符（East Asian Width 为 W/F）按 2 列计，
   其余按 1 列计，保证中英文混排时按"显示列"对齐而不是按字符数。
2. 列边界推断：统计每个显示列上"为空白"的行数占比（空白画像），
   连续的高空白占比列聚成"空白带"，即列边界。允许少量行穿越空白带
   （这些行就是跨列合并的痕迹），容忍度由 tolerance 控制。
3. 切分与合并：按边界切分每一行；某行在空白带内有非空白字符时，
   视为跨列（colspan），把相邻单元格合并；单元格内容为跨行标记
   （如 ^^、↑、〃、"）时与正上方单元格合并（rowspan）。
4. 一致性校验：每一行的有效列数（sum(colspan)）必须等于推断出的
   总列数，不一致时报告具体行号与偏差量。
"""

from __future__ import annotations

import json
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# 默认的"与上方单元格合并"标记（跨行合并的痕迹）
DEFAULT_ROW_SPAN_MARKERS = ("^^", "↑", "〃", '"')

# 构成表格分隔线（rule line）的字符
_RULE_CHARS = set("-=+~_| ")


# ---------------------------------------------------------------- 显示宽度

def char_width(ch: str) -> int:
    """单个字符的显示宽度：全角/宽字符为 2，其余为 1。"""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """字符串的显示宽度（全角字符按 2 计）。"""
    return sum(char_width(c) for c in text)


def _char_positions(line: str) -> list[tuple[str, int, int]]:
    """返回 [(字符, 起始显示列, 结束显示列), ...]（结束列为开区间）。"""
    result = []
    col = 0
    for ch in line:
        w = char_width(ch)
        result.append((ch, col, col + w))
        col += w
    return result


def is_rule_line(line: str) -> bool:
    """是否为由 -=+~_| 和空白构成的表格分隔线（如 ---+---）。"""
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    return all(c in _RULE_CHARS for c in stripped) and any(
        c in "-=~_" for c in stripped
    )


# ---------------------------------------------------------------- 数据结构

@dataclass
class Boundary:
    """一个列边界（空白带）：显示列区间 [start, end)。"""
    start: int
    end: int

    @property
    def width(self) -> int:
        return self.end - self.start


@dataclass
class Span:
    """一处合并单元格：锚点在 (row, col)，向下/向右扩展。"""
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""


@dataclass
class RowError:
    """行级错误：列数不一致或合并标记悬空。"""
    line_no: int          # 1 起始，相对输入文本（含被跳过的空行）
    kind: str             # "column_count" | "orphan_row_span"
    expected: int = 0
    actual: int = 0
    message: str = ""

    @property
    def deviation(self) -> int:
        return self.actual - self.expected


@dataclass
class ParseResult:
    """解析结果。

    rows[r][c] 为单元格文本；被合并覆盖的位置为 None。
    spans 记录所有合并（含 1x1 的普通单元格之外的合并）。
    """
    rows: list[list[Optional[str]]]
    spans: list[Span]
    boundaries: list[Boundary]
    errors: list[RowError]
    profile: list[int] = field(default_factory=list)  # 每个显示列的空白行数
    n_lines: int = 0

    @property
    def n_cols(self) -> int:
        return len(self.boundaries) + 1

    def to_dict(self) -> dict:
        return {
            "n_cols": self.n_cols,
            "rows": self.rows,
            "spans": [vars(s) for s in self.spans],
            "boundaries": [vars(b) for b in self.boundaries],
            "errors": [vars(e) for e in self.errors],
        }


# ---------------------------------------------------------------- 边界推断

def blank_profile(lines: list[str]) -> list[int]:
    """空白画像：profile[c] = 第 c 个显示列上为空白的行数。

    某行在该列"为空白"指：该列超出该行显示宽度，或对应字符是空格。
    全角字符占两列，两列都按该字符计（非空白）。
    """
    width = 0
    maps = []
    for line in lines:
        positions = _char_positions(line)
        col_of = {}
        for ch, start, end in positions:
            for c in range(start, end):
                col_of[c] = ch
        maps.append((col_of, positions[-1][2] if positions else 0))
        width = max(width, maps[-1][1])
    profile = [0] * width
    for col_of, line_width in maps:
        for c in range(width):
            if c >= line_width or col_of.get(c, " ") == " ":
                profile[c] += 1
    return profile


def infer_boundaries(
    lines: list[str],
    tolerance: Optional[int] = None,
    min_gap_width: int = 1,
    min_support_rows: int = 2,
) -> tuple[list[Boundary], list[int]]:
    """推断列边界。

    判据（两段式）：
    1. 严格空白带：在"所有行"上都为空白的显示列，聚成簇。短行的行尾
       之外不计数；每个边界还必须被至少 min_support_rows 行支持
       （这些行在边界左右两侧都有非空白内容），避免短行/边缘空白误判。
    2. 放宽空白带：tolerance（默认 n//3）是允许穿越空白带的行数，
       用于识别跨列合并——个别行的内容连续地盖过空白带。放宽边界
       必须同时满足：阻挡它的行数不超过 tolerance；每个阻挡行的内容
       在该行内"连续穿越"整个空白带（带前一列和带后一列都是非空白，
       且带内无空格），防止把短值列内部的空白误当成分隔。
       放宽边界与严格边界重叠时，以严格边界为准。
    """
    n = len(lines)
    if tolerance is None:
        tolerance = max(0, n // 3)
    min_support_rows = min(min_support_rows, max(1, n - 1))
    profile = blank_profile(lines)

    rows_info = []
    for line in lines:
        positions = _char_positions(line)
        content = [(s, e) for ch, s, e in positions if ch != " "]
        left = min((s for s, e in content), default=0)
        right = max((e for s, e in content), default=0)
        covered = set()
        for s_, e_ in content:
            covered.update(range(s_, e_))
        rows_info.append((left, right, covered))

    def make_clusters(blank_count: int) -> list[list[int]]:
        cols = [c for c in range(len(profile)) if profile[c] >= blank_count]
        clusters: list[list[int]] = []
        for c in cols:
            if clusters and c == clusters[-1][-1] + 1:
                clusters[-1].append(c)
            else:
                clusters.append([c])
        if clusters and clusters[0][0] == 0:
            clusters.pop(0)
        if clusters and clusters[-1][-1] == len(profile) - 1:
            clusters.pop()
        return clusters

    def supported(cl: list[int], blockers: set[int]) -> bool:
        start, end = cl[0], cl[-1] + 1
        count = 0
        for r, (left, right, _) in enumerate(rows_info):
            if r in blockers:
                continue
            if left < start and right > end:
                count += 1
        return count >= min_support_rows

    def blocking_rows(cl: list[int]) -> set[int]:
        cols = set(cl)
        return {r for r, (_, _, covered) in enumerate(rows_info)
                if covered & cols}

    def crosses_continuously(r: int, cl: list[int]) -> bool:
        start, end = cl[0], cl[-1] + 1
        _, _, covered = rows_info[r]
        # 带前一列和整个带被非空白连续覆盖（内容可止于带右边缘）
        return all(c in covered for c in range(start - 1, end))

    def build(blank_count: int, strict: bool) -> list[Boundary]:
        out = []
        for cl in make_clusters(blank_count):
            if len(cl) < min_gap_width:
                continue
            blockers = blocking_rows(cl)
            if not strict:
                if len(blockers) > tolerance:
                    continue
                if any(not crosses_continuously(r, cl) for r in blockers):
                    continue
            if supported(cl, blockers):
                out.append(Boundary(cl[0], cl[-1] + 1))
        return out

    strict_bounds = build(n, strict=True)
    if tolerance <= 0:
        return strict_bounds, profile
    relaxed_bounds = build(n - tolerance, strict=False)
    final = list(strict_bounds)
    for rb in relaxed_bounds:
        if not any(not (rb.end <= sb.start or rb.start >= sb.end)
                   for sb in strict_bounds):
            final.append(rb)
    final.sort(key=lambda b: b.start)
    return final, profile


# ---------------------------------------------------------------- 切分与合并

def _slice_row(
    line: str, boundaries: list[Boundary]
) -> tuple[list[str], list[bool], list[tuple[int, Optional[int]]]]:
    """按边界切分一行。

    返回 (各列文本, 每个边界是否被内容穿越, 每列的显示列区间)。
    """
    positions = _char_positions(line)
    cells: list[str] = []
    ranges: list[tuple[int, Optional[int]]] = []
    for i in range(len(boundaries) + 1):
        start = boundaries[i - 1].end if i > 0 else 0
        end = boundaries[i].start if i < len(boundaries) else None
        chars = [ch for ch, s, e in positions if s >= start and (end is None or s < end)]
        cells.append("".join(chars).strip())
        ranges.append((start, end))
    crossed = []
    for b in boundaries:
        hit = any(
            ch != " " and s < b.end and e > b.start
            for ch, s, e in positions
        )
        crossed.append(hit)
    return cells, crossed, ranges


def parse(
    text: str,
    *,
    tolerance: Optional[int] = None,
    min_gap_width: int = 1,
    row_span_markers: tuple[str, ...] = DEFAULT_ROW_SPAN_MARKERS,
    skip_rule_lines: bool = True,
) -> ParseResult:
    """解析纯文本对齐表格，返回 ParseResult。"""
    raw_lines = text.splitlines()
    # 记录原始行号（1 起始），跳过空行与分隔线
    lines: list[str] = []
    line_nos: list[int] = []
    for no, raw in enumerate(raw_lines, 1):
        if not raw.strip():
            continue
        if skip_rule_lines and is_rule_line(raw):
            continue
        lines.append(raw.rstrip("\n"))
        line_nos.append(no)

    errors: list[RowError] = []
    if not lines:
        return ParseResult([], [], [], errors, [], 0)

    boundaries, profile = infer_boundaries(lines, tolerance, min_gap_width)
    n_cols = len(boundaries) + 1

    # 1) 切分 + 跨列合并
    # grid[r] 长度恒为 n_cols，每列一项：(text|None, colspan)；
    # 被合并覆盖的位置为 (None, 0)
    grid: list[list[tuple[Optional[str], int]]] = []
    for line in lines:
        cells, crossed, ranges = _slice_row(line, boundaries)
        positions = _char_positions(line)
        entries: list[tuple[Optional[str], int]] = []
        c = 0
        while c < n_cols:
            span = 1
            while c + span - 1 < len(crossed) and crossed[c + span - 1]:
                span += 1
            if span > 1:
                # 合并时把落在空白带里的字符也收进来，避免内容丢失
                start = ranges[c][0]
                end = ranges[c + span - 1][1]
                merged = "".join(
                    ch for ch, s, e in positions
                    if s >= start and (end is None or s < end)
                ).strip()
                entries.append((merged or None, span))
                entries.extend([(None, 0)] * (span - 1))
            else:
                entries.append((cells[c], 1))  # 空单元格为 ""，与被覆盖的 None 区分
            c += span
        grid.append(entries)

    # 2) 跨行合并：内容为标记的单元格并入正上方单元格
    spans: list[Span] = []
    # 先登记跨列合并
    for r, row in enumerate(grid):
        for c, (text_, colspan) in enumerate(row):
            if colspan > 1 and text_ is not None:
                spans.append(Span(r, c, 1, colspan, text_))
    vmerged: set[tuple[int, int]] = set()  # 被跨行合并覆盖的 (行, 列)
    for r, row in enumerate(grid):
        for c, (text_, colspan) in enumerate(row):
            if colspan != 1 or text_ is None:
                continue
            if text_.strip() in row_span_markers:
                # 向上跳过已被跨行合并覆盖的格子，找到真正的锚点
                anchor = r - 1
                while (anchor, c) in vmerged:
                    anchor -= 1
                if anchor < 0 or grid[anchor][c][1] == 0 or grid[anchor][c][0] is None:
                    errors.append(RowError(
                        line_nos[r], "orphan_row_span",
                        message=f"第 {line_nos[r]} 行第 {c + 1} 列的跨行标记 "
                                f"{text_!r} 上方没有可合并的单元格",
                    ))
                    continue
                grid[r][c] = (None, 0)
                vmerged.add((r, c))
                # 更新/登记 rowspan
                for s in spans:
                    if s.row == anchor and s.col == c:
                        s.rowspan += 1
                        break
                else:
                    spans.append(Span(anchor, c, 2, 1, grid[anchor][c][0]))

    # 3) 展开为最终行 + 一致性校验（每行有效列数必须等于推断列数）
    rows: list[list[Optional[str]]] = []
    for r, row in enumerate(grid):
        flat: list[Optional[str]] = [text_ for text_, _ in row]
        effective = len(row)  # grid[r] 每列一项，长度即有效列数
        if effective != n_cols:
            errors.append(RowError(
                line_nos[r], "column_count", n_cols, effective,
                f"第 {line_nos[r]} 行有效列数 {effective}，"
                f"应为 {n_cols}（偏差 {effective - n_cols:+d}）",
            ))
        rows.append(flat)

    return ParseResult(rows, spans, boundaries, errors, profile, len(lines))


def validate(rows: list[list[object]]) -> list[str]:
    """独立校验：检查每行列数是否一致，返回问题描述列表。"""
    if not rows:
        return []
    expected = len(rows[0])
    problems = []
    for i, row in enumerate(rows):
        if len(row) != expected:
            problems.append(
                f"第 {i + 1} 行列数 {len(row)}，应为 {expected}"
                f"（偏差 {len(row) - expected:+d}）"
            )
    return problems


# ---------------------------------------------------------------- CLI

def _format_grid(result: ParseResult) -> str:
    rows = [["" if c is None else c for c in row] for row in result.rows]
    if not rows:
        return "(空表格)"
    widths = [
        max(display_width(row[c]) for row in rows) for c in range(result.n_cols)
    ]
    out = []
    for row in rows:
        parts = []
        for c, cell in enumerate(row):
            pad = widths[c] - display_width(cell)
            parts.append(cell + " " * pad)
        out.append(" | ".join(parts).rstrip())
    return "\n".join(out)


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="解析纯文本对齐表格")
    ap.add_argument("file", help="输入文本文件（- 表示标准输入）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结构化结果")
    ap.add_argument("--tolerance", type=int, default=None,
                    help="允许穿越空白带的行数（默认 行数//3）")
    ap.add_argument("--min-gap-width", type=int, default=1,
                    help="空白带最小宽度（显示列）")
    args = ap.parse_args(argv)

    text = sys.stdin.read() if args.file == "-" else open(
        args.file, encoding="utf-8").read()
    result = parse(text, tolerance=args.tolerance,
                   min_gap_width=args.min_gap_width)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_grid(result))
        if result.spans:
            print("\n合并单元格:")
            for s in result.spans:
                kind = []
                if s.rowspan > 1:
                    kind.append(f"跨 {s.rowspan} 行")
                if s.colspan > 1:
                    kind.append(f"跨 {s.colspan} 列")
                print(f"  行{s.row + 1} 列{s.col + 1} "
                      f"({'、'.join(kind)}): {s.text!r}")
        if result.errors:
            print("\n错误:", file=sys.stderr)
            for e in result.errors:
                print(f"  {e.message}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
