#!/usr/bin/env python3
"""大区域求值耗时基准。

场景 A：N x N 公式网格，每个单元格 = 上方单元格 + 左方单元格（链式依赖，
        验证拓扑序求值的吞吐）。
场景 B：一个 SUM 引用 N x N 大区域（验证矩形行优先展开的吞吐）。
场景 C：N x N 常量区域 + 每行一个 SUM 行区域（混合）。

用法：python3 scripts/benchmark.py [--full]
  默认规模 10k / 100k 单元格；--full 追加 1M 规模。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from formulas import Workbook, evaluate_workbook
from formulas.cellref import num_to_col


def build_chain_grid(n):
    """N x N 网格：A1=1，其余 = 上 + 左（边界只取存在的）。"""
    wb = Workbook()
    s = wb.add_sheet("G")
    for r in range(1, n + 1):
        for c in range(1, n + 1):
            addr = "%s%d" % (num_to_col(c), r)
            if r == 1 and c == 1:
                s.set(addr, 1)
            else:
                parts = []
                if r > 1:
                    parts.append("%s%d" % (num_to_col(c), r - 1))
                if c > 1:
                    parts.append("%s%d" % (num_to_col(c - 1), r))
                s.set(addr, "=" + "+".join(parts))
    return wb


def build_big_sum(n):
    """N x N 常量 + 一个 SUM 全区域公式。"""
    wb = Workbook()
    s = wb.add_sheet("D")
    for r in range(1, n + 1):
        for c in range(1, n + 1):
            s.set("%s%d" % (num_to_col(c), r), (r * 31 + c) % 97)
    last = "%s%d" % (num_to_col(n), n)
    s.set("A%d" % (n + 2), "=SUM(A1:%s)" % last)
    return wb


def build_row_sums(n):
    """N x N 常量 + 每行一个 SUM 行区域。"""
    wb = Workbook()
    s = wb.add_sheet("R")
    for r in range(1, n + 1):
        for c in range(1, n + 1):
            s.set("%s%d" % (num_to_col(c), r), (r + c) % 13)
        s.set("%s%d" % (num_to_col(n + 2), r),
              "=SUM(A%d:%s%d)" % (r, num_to_col(n), r))
    return wb


def run_case(name, builder, n, check=None):
    t0 = time.perf_counter()
    wb = builder(n)
    t1 = time.perf_counter()
    values, cycles = evaluate_workbook(wb)
    t2 = time.perf_counter()
    assert not cycles
    if check is not None:
        check(wb, values, n)
    cells = n * n
    return {
        "case": name,
        "grid": "%dx%d" % (n, n),
        "cells": cells,
        "build_s": round(t1 - t0, 3),
        "eval_s": round(t2 - t1, 3),
        "cells_per_s": int(cells / (t2 - t1)) if t2 > t1 else None,
    }


def check_chain(wb, values, n):
    # 链式网格 (r,c) 的值 = r*c（组合数递推：上+左 => 二项式）
    from math import comb
    got = values[("G", n, n)]
    want = comb(2 * n - 2, n - 1)
    assert got == want, (got, want)


def main():
    full = "--full" in sys.argv
    sizes = [100, 300] + ([1000] if full else [])
    results = []
    for n in sizes:
        results.append(run_case("A:链式公式网格(拓扑序)", build_chain_grid, n,
                                check=check_chain if n <= 300 else None))
        results.append(run_case("B:单SUM大区域", build_big_sum, n))
        results.append(run_case("C:每行SUM行区域", build_row_sums, n))
        print("完成 n=%d" % n, flush=True)

    out = os.path.join(os.path.dirname(__file__), "..", "data", "benchmark_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"python": sys.version.split()[0], "results": results}, f,
                  ensure_ascii=False, indent=2)

    print("\n%-26s %-9s %10s %9s %9s %14s" % ("场景", "网格", "单元格数", "建图s", "求值s", "格/秒"))
    for r in results:
        print("%-26s %-9s %10d %9.3f %9.3f %14s" % (
            r["case"], r["grid"], r["cells"], r["build_s"], r["eval_s"],
            format(r["cells_per_s"], ",")))
    print("\n结果已写入 %s" % os.path.abspath(out))


if __name__ == "__main__":
    main()
