"""逐组合对拍：原始实现 vs 表驱动重构实现。

对全部 2592 个条件组合，比较两个实现的输出（返回值或异常类型+消息），
任一组合不一致即以非零码退出，并输出 diff 报告。

用法：python3 tools/diff_test.py
产物：out/diff_report.csv
"""

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from enumerate_table import all_combinations, COLUMNS

import legacy_pricing
import pricing


def describe(func, order):
    try:
        return "OK %.2f" % func(order)
    except Exception as exc:
        return "ERR %s: %s" % (type(exc).__name__, exc)


def main():
    mismatches = []
    rows = []
    total = 0
    for combo in all_combinations():
        total += 1
        old = describe(legacy_pricing.quote, dict(combo))
        new = describe(pricing.quote, dict(combo))
        status = "PASS" if old == new else "FAIL"
        rows.append([combo[c] for c in COLUMNS] + [old, new, status])
        if old != new:
            mismatches.append((combo, old, new))

    out_path = os.path.join(os.path.dirname(__file__), "..", "out", "diff_report.csv")
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS + ["legacy_output", "refactored_output", "result"])
        for row in rows:
            writer.writerow(row)

    print("total combinations : %d" % total)
    print("matched            : %d" % (total - len(mismatches)))
    print("mismatched         : %d" % len(mismatches))
    print("report             : %s" % os.path.relpath(out_path))
    for combo, old, new in mismatches[:20]:
        print("MISMATCH %r\n  legacy:     %s\n  refactored: %s" % (combo, old, new))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
