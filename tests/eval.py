#!/usr/bin/env python3
"""人工标注对拍：逐字段比对，输出准确率/漏抽/误抽与逐样例规则命中。

用法：python3 -m tests.eval            # 终端摘要
     python3 -m tests.eval --report   # 同时写 report/accuracy_report.md
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logistics_extractor import extract  # noqa: E402

FIELDS = ["name", "phones", "province", "city", "district", "detail"]
FIELD_CN = {"name": "收件人", "phones": "电话", "province": "省",
            "city": "市", "district": "区县", "detail": "详细地址"}

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(HERE, "gold.json")


def norm_phones(vals):
    return sorted(vals)


def field_match(field, got, gold):
    if field == "phones":
        return norm_phones(got) == norm_phones(gold)
    return got == gold


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="写 report/accuracy_report.md")
    ap.add_argument("--case", type=int, default=0, help="只跑某条样例并打印规则命中")
    args = ap.parse_args()

    with open(GOLD, encoding="utf-8") as f:
        samples = json.load(f)

    if args.case:
        samples = [s for s in samples if s["id"] == args.case]
        if samples:
            from logistics_extractor import explain
            print(explain(samples[0]["text"]))
        return

    totals = {f: 0 for f in FIELDS}
    misses = {f: [] for f in FIELDS}   # 漏抽/错值（应抽而未抽到或不符）
    extras = {f: [] for f in FIELDS}   # 误抽（不该有却有）
    case_ok = 0
    records = []

    for s in samples:
        r = extract(s["text"])
        got = {
            "name": r.name,
            "phones": [p for p, _ in r.phones],
            "province": r.province,
            "city": r.city,
            "district": r.district,
            "detail": r.detail,
        }
        all_ok = True
        problems = []
        for f in FIELDS:
            gold_v = s[f]
            got_v = got[f]
            if field_match(f, got_v, gold_v):
                totals[f] += 1
            else:
                all_ok = False
                problems.append(f)
                if isinstance(gold_v, list):
                    missing = set(gold_v) - set(got_v)
                    extra = set(got_v) - set(gold_v)
                    if missing:
                        misses[f].append((s["id"], missing, got_v))
                    if extra:
                        extras[f].append((s["id"], extra, got_v))
                elif gold_v and not got_v:
                    misses[f].append((s["id"], gold_v, got_v))
                elif not gold_v and got_v:
                    extras[f].append((s["id"], gold_v, got_v))
                else:
                    misses[f].append((s["id"], gold_v, got_v))
        if all_ok:
            case_ok += 1
        records.append((s, got, problems, r))

    n = len(samples)
    total_fields = n * len(FIELDS)
    acc_fields = sum(totals.values())

    lines = []
    lines.append("# 抽取对拍报告")
    lines.append("")
    lines.append("样例数：%d（%s）" % (n, "全部 6 字段正确即整条正确"))
    lines.append("")
    lines.append("## 准确率")
    lines.append("")
    lines.append("| 字段 | 正确 | 准确率 |")
    lines.append("|------|------|--------|")
    for f in FIELDS:
        lines.append("| %s | %d/%d | %.1f%% |" % (FIELD_CN[f], totals[f], n, 100.0 * totals[f] / n))
    lines.append("| **字段合计** | %d/%d | **%.1f%%** |" % (acc_fields, total_fields, 100.0 * acc_fields / total_fields))
    lines.append("| **整条样例** | %d/%d | **%.1f%%** |" % (case_ok, n, 100.0 * case_ok / n))
    lines.append("")

    lines.append("## 漏抽 / 错值清单")
    lines.append("")
    any_miss = False
    for f in FIELDS:
        if misses[f]:
            any_miss = True
            lines.append("### %s" % FIELD_CN[f])
            for cid, gold_v, got_v in misses[f]:
                lines.append("- #%d 标注=%r 抽取=%r" % (cid, gold_v, got_v))
    if not any_miss:
        lines.append("无。")
    lines.append("")

    lines.append("## 误抽清单（标注为空却抽出值 / 抽出多余电话）")
    lines.append("")
    any_extra = False
    for f in FIELDS:
        if extras[f]:
            any_extra = True
            lines.append("### %s" % FIELD_CN[f])
            for cid, gold_v, got_v in extras[f]:
                lines.append("- #%d 标注=%r 抽取=%r" % (cid, gold_v, got_v))
    if not any_extra:
        lines.append("无。")
    lines.append("")

    lines.append("## 逐样例结果")
    lines.append("")
    lines.append("| # | 标签 | 收件人 | 电话 | 省 | 市 | 区县 | 详细地址 | 地址置信 | 问题字段 |")
    lines.append("|---|------|--------|------|----|----|------|----------|----------|----------|")
    for s, got, problems, r in records:
        row = [
            str(s["id"]), "/".join(s["tags"]), got["name"],
            "|".join(got["phones"]), got["province"], got["city"],
            got["district"], got["detail"], "%.2f" % r.address_conf,
            ",".join(FIELD_CN[p] for p in problems) or "",
        ]
        lines.append("| " + " | ".join(x.replace("|", "/") for x in row) + " |")
    lines.append("")

    report = "\n".join(lines)
    print(report)

    if args.report:
        out_dir = os.path.join(os.path.dirname(HERE), "report")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "accuracy_report.md"), "w", encoding="utf-8") as f:
            f.write(report)
        # 逐样例规则命中
        with open(os.path.join(out_dir, "rule_hits.txt"), "w", encoding="utf-8") as f:
            for s, got, problems, r in records:
                f.write("#%d %s\n" % (s["id"], "/".join(s["tags"])))
                f.write("输入: %s\n" % s["text"])
                for h in r.rules:
                    f.write("  %s\n" % h)
                f.write("\n")
        print("\n报告已写入 report/accuracy_report.md 与 report/rule_hits.txt")


if __name__ == "__main__":
    main()
