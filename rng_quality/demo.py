"""命令行演示：对固定样本集执行评估并打印结果表。

运行：python3 -m rng_quality.demo
退出码：所有样本中，已知正常样本 PASS 且所有做坏样本 FAIL 时为 0，否则为 1。
"""

import sys

from .core import assess
from .samples import SAMPLE_BYTES, make_samples

# 每个样本的预期结论：正常样本应 PASS，做坏样本应 FAIL。
EXPECTED = {
    "good_sha256_counter": "PASS",
    "low_nibble_fixed": "FAIL",
    "period_2bytes": "FAIL",
    "period_16bytes": "FAIL",
    "all_zeros": "FAIL",
}


def format_table(report_by_name):
    checks = ["uniformity", "bit_balance", "runs", "periodicity"]
    header = ["sample", "bytes"] + checks + ["overall", "expect", "verdict"]
    rows = []
    for name, report in report_by_name.items():
        by_check = {r["check"]: r["status"] for r in report["results"]}
        expect = EXPECTED[name]
        verdict = "OK" if report["overall"] == expect else "MISMATCH"
        rows.append(
            [name, str(report["n_bytes"])]
            + [by_check[c] for c in checks]
            + [report["overall"], expect, verdict]
        )
    widths = [max(len(row[i]) for row in [header] + rows) for i in range(len(header))]
    lines = []
    for row in [header] + rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def main():
    samples = make_samples(SAMPLE_BYTES)
    reports = {name: assess(data) for name, data in samples.items()}
    print(format_table(reports))
    print()
    for name, report in reports.items():
        print("[%s] overall=%s" % (name, report["overall"]))
        for r in report["results"]:
            print("  - %-11s %-4s %s" % (r["check"], r["status"], r["detail"]))
    ok = all(report["overall"] == EXPECTED[name] for name, report in reports.items())
    print()
    print("总体结论：%s" % ("符合预期" if ok else "存在与预期不符的样本"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
