"""A1 风格单元格地址与区域（矩形范围）的解析和遍历。

地址形如 ``B3``；区域形如 ``A1:C9``，可以带工作表限定：
``Sheet2!B3``、``'My Sheet'!A1:C9``。列号从 1 开始（A=1）。
"""

import re

_CELL_RE = re.compile(r"^([A-Za-z]{1,4})([1-9][0-9]*)$")


def col_to_num(letters):
    """列字母 -> 列号（A=1, B=2, ..., AA=27）。"""
    num = 0
    for ch in letters.upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError("非法列名: %r" % letters)
        num = num * 26 + (ord(ch) - ord("A") + 1)
    return num


def num_to_col(num):
    """列号 -> 列字母。"""
    if num < 1:
        raise ValueError("列号必须 >= 1: %r" % num)
    out = []
    while num:
        num, rem = divmod(num - 1, 26)
        out.append(chr(ord("A") + rem))
    return "".join(reversed(out))


def parse_cell(text):
    """把 ``B3`` 解析为 (row, col)，行列均从 1 开始。"""
    m = _CELL_RE.match(text)
    if not m:
        raise ValueError("非法单元格地址: %r" % text)
    return int(m.group(2)), col_to_num(m.group(1))


def format_cell(row, col):
    return "%s%d" % (num_to_col(col), row)


def split_sheet_ref(text):
    """拆分可选的工作表限定。返回 (sheet 或 None, 主体)。

    支持 ``Sheet2!A1`` 与 ``'My Sheet'!A1``（引号内 ``''`` 表示一个字面单引号）。
    """
    if text.startswith("'"):
        end = 1
        name = []
        while end < len(text):
            ch = text[end]
            if ch == "'":
                if end + 1 < len(text) and text[end + 1] == "'":
                    name.append("'")
                    end += 2
                    continue
                break
            name.append(ch)
            end += 1
        if end >= len(text) or end + 1 >= len(text) or text[end + 1] != "!":
            raise ValueError("非法工作表限定: %r" % text)
        return "".join(name), text[end + 2:]
    if "!" in text:
        sheet, body = text.split("!", 1)
        return sheet, body
    return None, text


class CellRef:
    """单个单元格引用，可带工作表限定（None 表示当前表）。"""

    __slots__ = ("sheet", "row", "col")

    def __init__(self, row, col, sheet=None):
        self.sheet = sheet
        self.row = row
        self.col = col

    @classmethod
    def parse(cls, text):
        sheet, body = split_sheet_ref(text)
        row, col = parse_cell(body)
        return cls(row, col, sheet)

    def resolve(self, current_sheet):
        """返回规范化的 (sheet, row, col)；未限定时取当前表。"""
        return (self.sheet if self.sheet is not None else current_sheet, self.row, self.col)

    def __repr__(self):
        prefix = ("%s!" % self.sheet) if self.sheet else ""
        return "CellRef(%s%s)" % (prefix, format_cell(self.row, self.col))

    def __eq__(self, other):
        return (
            isinstance(other, CellRef)
            and (self.sheet, self.row, self.col) == (other.sheet, other.row, other.col)
        )

    def __hash__(self):
        return hash((self.sheet, self.row, self.col))


class Range:
    """矩形区域引用。遍历顺序固定为行优先（先行后列）。"""

    __slots__ = ("sheet", "row1", "col1", "row2", "col2")

    def __init__(self, row1, col1, row2, col2, sheet=None):
        self.sheet = sheet
        self.row1, self.col1 = min(row1, row2), min(col1, col2)
        self.row2, self.col2 = max(row1, row2), max(col1, col2)

    @classmethod
    def parse(cls, text):
        sheet, body = split_sheet_ref(text)
        if ":" not in body:
            raise ValueError("非法区域: %r" % text)
        a, b = body.split(":", 1)
        r1, c1 = parse_cell(a)
        r2, c2 = parse_cell(b)
        return cls(r1, c1, r2, c2, sheet)

    @property
    def height(self):
        return self.row2 - self.row1 + 1

    @property
    def width(self):
        return self.col2 - self.col1 + 1

    @property
    def size(self):
        return self.height * self.width

    def addresses(self, current_sheet=None):
        """按矩形行优先顺序产出 (sheet, row, col)。"""
        sheet = self.sheet if self.sheet is not None else current_sheet
        for row in range(self.row1, self.row2 + 1):
            for col in range(self.col1, self.col2 + 1):
                yield (sheet, row, col)

    def contains(self, row, col):
        return self.row1 <= row <= self.row2 and self.col1 <= col <= self.col2

    def __repr__(self):
        prefix = ("%s!" % self.sheet) if self.sheet else ""
        return "Range(%s%s:%s)" % (
            prefix,
            format_cell(self.row1, self.col1),
            format_cell(self.row2, self.col2),
        )

    def __eq__(self, other):
        return isinstance(other, Range) and (
            self.sheet, self.row1, self.col1, self.row2, self.col2
        ) == (other.sheet, other.row1, other.col1, other.row2, other.col2)

    def __hash__(self):
        return hash((self.sheet, self.row1, self.col1, self.row2, self.col2))
