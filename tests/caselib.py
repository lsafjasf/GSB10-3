"""把 JSON 用例装载成 Workbook，并给出统一的结果比较工具。"""

import json
import os

from formulas import Workbook, evaluate_workbook, CellError, col_to_num, parse_cell

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_json(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def set_cell(sheet, address, value):
    if isinstance(value, dict) and "formula" in value:
        sheet.set(address, value["formula"])
    elif isinstance(value, bool):
        sheet.set(address, value)
    else:
        sheet.set(address, value)


def build_workbook(case):
    wb = Workbook()
    for sheet_name, cells in case["sheets"].items():
        sheet = wb.add_sheet(sheet_name)
        for address, value in cells.items():
            set_cell(sheet, address, value)
    return wb


def expected_value(v):
    if isinstance(v, str) and v.startswith("#"):
        return CellError(v)
    return v


def run_eval_case(case):
    wb = build_workbook(case)
    values, cycles = evaluate_workbook(wb)
    results = {}
    for key, want in case["expect"].items():
        sheet, addr = key.split("!", 1)
        row, col = parse_cell(addr)
        got = values[(sheet, row, col)]
        results[key] = (got, expected_value(want))
    return results, cycles


def values_by_address(values):
    from formulas.cellref import format_cell
    out = {}
    for (sheet, row, col), v in values.items():
        out["%s!%s" % (sheet, format_cell(row, col))] = v
    return out
