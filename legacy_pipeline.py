"""重构前的遗留实现：一段串行的同步处理。

问题：
- 解析、换算、聚合、写报告、写审计全部揉在一个函数里，无法观测中间状态；
- 任何一步出错只能整体重来，且审计日志是追加写，重跑会产生重复副作用；
- 没有阶段概念，无法单独重试或跳过某一步。
"""
import argparse
import csv

RATES = {"USD": 1.0, "EUR": 1.1, "CNY": 0.14, "JPY": 0.0067}


def process(input_path, output_path, audit_path, run_id):
    records = []
    skipped = 0
    with open(input_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                amount = float(row["amount"])
                currency = row["currency"].strip().upper()
                if currency not in RATES:
                    raise ValueError("unknown currency: %s" % currency)
                customer = row["customer"].strip()
                if not customer:
                    raise ValueError("empty customer")
                records.append({
                    "order_id": row["order_id"].strip(),
                    "customer": customer,
                    "amount": amount,
                    "currency": currency,
                })
            except (KeyError, ValueError, AttributeError):
                skipped += 1

    for record in records:
        record["usd"] = round(record["amount"] * RATES[record["currency"]], 2)

    totals = {}
    for record in records:
        customer = record["customer"]
        totals[customer] = round(totals.get(customer, 0.0) + record["usd"], 2)

    grand_total = 0.0
    for customer in sorted(totals):
        grand_total = round(grand_total + totals[customer], 2)

    lines = [
        "Sales Report",
        "============",
        "run: %s" % run_id,
        "orders: %d" % len(records),
        "skipped: %d" % skipped,
        "------------",
    ]
    for customer in sorted(totals):
        lines.append("%s: %.2f" % (customer, totals[customer]))
    lines.append("------------")
    lines.append("total: %.2f" % grand_total)
    report = "\n".join(lines) + "\n"

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write("run=%s processed=%d skipped=%d\n" % (run_id, len(records), skipped))
    return report


def main():
    parser = argparse.ArgumentParser(description="legacy serial pipeline")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    process(args.input, args.output, args.audit, args.run_id)


if __name__ == "__main__":
    main()
