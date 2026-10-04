"""电子表格错误值类型。

公式求值中出现的错误用 CellError 表示（而不是抛异常），
这样错误可以像 Excel 一样沿依赖链传播。
"""

ERROR_LITERALS = ("#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#NULL!", "#CYCLE!")


class CellError(Exception):
    """可作为值在单元格之间传播的错误。"""

    def __init__(self, code):
        if code not in ERROR_LITERALS:
            raise ValueError("未知错误码: %r" % code)
        super().__init__(code)
        self.code = code

    def __repr__(self):
        return "CellError(%r)" % self.code

    def __str__(self):
        return self.code

    def __eq__(self, other):
        return isinstance(other, CellError) and other.code == self.code

    def __hash__(self):
        return hash(self.code)


def div_by_zero():
    return CellError("#DIV/0!")


def value_error():
    return CellError("#VALUE!")


def name_error():
    return CellError("#NAME?")


def ref_error():
    return CellError("#REF!")


def num_error():
    return CellError("#NUM!")


def cycle_error():
    return CellError("#CYCLE!")
