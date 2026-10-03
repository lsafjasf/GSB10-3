#!/usr/bin/env python3
"""运行演示：配对冲突报告 + 全/半角转换对照，并生成 data/conversion_report.md。

用法:
    python3 demo.py            # 终端打印报告并刷新 data/conversion_report.md
    python3 demo.py --quiet    # 只生成报告文件，不打印
"""

from __future__ import annotations

import sys
from pathlib import Path

from punctnorm import diff_width, normalize_width, pair

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def _rows(path: str, cols: int):
    for line in (DATA / path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == cols:
            yield parts


def pairing_report(lines: list[str]) -> None:
    lines.append("## 一、配对冲突样例\n")
    for sample, note in _rows("conflict_samples.txt", 2):
        rpt = pair(sample, mode="report")
        fix = pair(sample, mode="fix")
        mark = pair(sample, mode="mark")
        lines.append(f"### `{sample}` — {note}\n")
        for iss in rpt.issues:
            lines.append(f"- 冲突报告：{iss.detail}")
        if not rpt.issues:
            lines.append("- 无冲突")
        lines.append(f"- fix（补齐/删除）：`{fix.text}`")
        lines.append(f"- mark（标注保留）：`{mark.text}`")
        lines.append("")


def width_report(lines: list[str]) -> None:
    lines.append("## 二、全角/半角转换对照数据\n")
    lines.append("| # | 转换前 | 转换后 | 规则 | 校验 |")
    lines.append("|---|--------|--------|------|------|")
    for idx, (before, expected, rule) in enumerate(_rows("conversion_cases.tsv", 3), 1):
        got = normalize_width(before)
        ok = "✅" if got == expected else f"❌ 实际 `{got}`"
        lines.append(f"| {idx} | `{before}` | `{got}` | {rule} | {ok} |")
    lines.append("")

    lines.append("### 逐字符变更明细\n")
    for before, _, _ in _rows("conversion_cases.tsv", 3):
        changes = diff_width(before)
        if not changes:
            continue
        lines.append(f"- `{before}`：")
        for c in changes:
            lines.append(f"  - 第{c.pos + 1}字符 `{c.before}` → `{c.after}`（{c.reason}）")
    lines.append("")


def edge_report(lines: list[str]) -> None:
    lines.append("## 三、边界用例结果\n")
    for category, sample, expect in _rows("edge_cases.tsv", 3):
        pr = pair(sample)
        lines.append(f"- {category} `{sample}`：{expect}；实际"
                     f"{'无冲突' if pr.ok else '有冲突'}，fix=`{pr.text}`")
    lines.append("")


def main() -> int:
    lines = ["# 标点规范化处理报告\n",
             "> 由 `python3 demo.py` 自动生成。\n"]
    pairing_report(lines)
    width_report(lines)
    edge_report(lines)
    report = "\n".join(lines)
    (DATA / "conversion_report.md").write_text(report, encoding="utf-8")
    if "--quiet" not in sys.argv:
        print(report)
        print("报告已写入 data/conversion_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
