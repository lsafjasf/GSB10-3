"""工作簿 / 工作表 / 单元格的数据模型。"""

from .cellref import parse_cell, format_cell


class Cell:
    __slots__ = ("raw",)

    def __init__(self, raw):
        self.raw = raw

    @property
    def is_formula(self):
        return isinstance(self.raw, str) and self.raw.startswith("=")


class Sheet:
    def __init__(self, name):
        self.name = name
        self.cells = {}  # (row, col) -> Cell

    def set(self, address, value):
        """value 为数字 / 字符串 / 布尔 / None，或以 '=' 开头的公式字符串。"""
        row, col = parse_cell(address) if isinstance(address, str) else address
        if value is None:
            self.cells.pop((row, col), None)
        else:
            self.cells[(row, col)] = Cell(value)

    def get(self, row, col):
        return self.cells.get((row, col))


class Workbook:
    def __init__(self):
        self.sheets = {}

    def add_sheet(self, name):
        if name in self.sheets:
            raise ValueError("工作表已存在: %r" % name)
        sheet = Sheet(name)
        self.sheets[name] = sheet
        return sheet

    def sheet(self, name):
        try:
            return self.sheets[name]
        except KeyError:
            raise KeyError("工作表不存在: %r" % name)

    def set(self, sheet, address, value):
        self.sheet(sheet).set(address, value)

    def formula_cells(self):
        """产出全部公式单元格：(sheet, row, col, formula)。"""
        for sname, sheet in self.sheets.items():
            for (row, col), cell in sheet.cells.items():
                if cell.is_formula:
                    yield (sname, row, col, cell.raw)
