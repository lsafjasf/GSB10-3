"""穷举全部条件组合，记录原始实现输出，生成决策表（CSV + 汇总）。

用法：python3 tools/enumerate_table.py
产物：out/decision_table.csv  （每一行 = 一个条件组合 + 原始实现输出）
"""

import csv
import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import legacy_pricing

# ---- 条件域（笛卡尔积即全部组合）----
USER_LEVELS = ["normal", "silver", "gold", "staff"]
REGIONS = ["domestic", "remote", "overseas"]
CATEGORIES = ["normal", "fragile", "frozen"]
EXPRESS = [False, True]
HOLIDAY = [False, True]
ACCOUNT_STATUS = ["active", "frozen", "banned"]
# 金额代表值覆盖全部区间：负数(报错) / 0(空单) / (0,500) / 免邮边界 500 / 大于 500
AMOUNTS = [-1.0, 0.0, 50.0, 499.99, 500.0, 1000.0]

COLUMNS = ["user_level", "region", "category", "express", "holiday",
           "account_status", "amount"]


def all_combinations():
    for combo in itertools.product(USER_LEVELS, REGIONS, CATEGORIES,
                                   EXPRESS, HOLIDAY, ACCOUNT_STATUS, AMOUNTS):
        yield dict(zip(COLUMNS, combo))


def run(func):
    """对 func 跑全部组合，返回 {组合tuple: 输出描述}。输出为 'OK <fee>' 或 'ERR <类型>: <消息>'。"""
    results = {}
    for combo in all_combinations():
        key = tuple(combo[c] for c in COLUMNS)
        try:
            results[key] = "OK %.2f" % func(dict(combo))
        except Exception as exc:  # 记录异常类型与消息，异常也是行为的一部分
            results[key] = "ERR %s: %s" % (type(exc).__name__, exc)
    return results


def main():
    results = run(legacy_pricing.quote)
    out_path = os.path.join(os.path.dirname(__file__), "..", "out", "decision_table.csv")
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS + ["legacy_output"])
        for key in sorted(results):
            writer.writerow(list(key) + [results[key]])

    total = len(results)
    errors = sum(1 for v in results.values() if v.startswith("ERR"))
    distinct = len(set(results.values()))
    print("combinations : %d" % total)
    print("error combos : %d" % errors)
    print("ok combos    : %d" % (total - errors))
    print("distinct outputs: %d" % distinct)
    print("written to   : %s" % os.path.relpath(out_path))


if __name__ == "__main__":
    main()
