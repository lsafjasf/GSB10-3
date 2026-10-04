"""订单处理的五个阶段。

每个阶段只读写 ctx.data 中显式声明的中间结果，副作用统一走 ctx.effects，
保证阶段可以单独重跑、跳过，且重试不会重复产生副作用。
"""
import csv

RATES = {"USD": 1.0, "EUR": 1.1, "CNY": 0.14, "JPY": 0.0067}


def load_stage(ctx):
    """阶段 1：解析 CSV -> records / skipped。"""
    records = []
    skipped = 0
    with open(ctx.data["input_path"], newline="", encoding="utf-8") as fh:
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
    ctx.data["records"] = records
    ctx.data["skipped"] = skipped


def normalize_stage(ctx):
    """阶段 2：按固定汇率把金额换算成 USD -> records[i].usd。"""
    for record in ctx.data["records"]:
        record["usd"] = round(record["amount"] * RATES[record["currency"]], 2)


def aggregate_stage(ctx):
    """阶段 3：按客户汇总 -> totals / grand_total。"""
    totals = {}
    for record in ctx.data["records"]:
        customer = record["customer"]
        totals[customer] = round(totals.get(customer, 0.0) + record["usd"], 2)
    grand_total = 0.0
    for customer in sorted(totals):
        grand_total = round(grand_total + totals[customer], 2)
    ctx.data["totals"] = totals
    ctx.data["grand_total"] = grand_total


def _write_file(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def report_stage(ctx):
    """阶段 4：渲染报告文本，并幂等地写出报告文件。"""
    run_id = ctx.data["run_id"]
    records = ctx.data["records"]
    skipped = ctx.data["skipped"]
    totals = ctx.data["totals"]
    grand_total = ctx.data["grand_total"]

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
    ctx.data["report"] = report

    output_path = ctx.data["output_path"]
    ctx.effects.apply(
        "write-report:%s" % output_path,
        lambda: _write_file(output_path, report),
    )


def _append_file(path, content):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(content)


def audit_stage(ctx):
    """阶段 5：幂等地追加一行审计日志。"""
    line = "run=%s processed=%d skipped=%d\n" % (
        ctx.data["run_id"],
        len(ctx.data["records"]),
        ctx.data["skipped"],
    )
    audit_path = ctx.data["audit_path"]
    ctx.effects.apply(
        "audit:%s:%s" % (audit_path, ctx.data["run_id"]),
        lambda: _append_file(audit_path, line),
    )


STAGE_NAMES = ["load", "normalize", "aggregate", "report", "audit"]


def default_stages():
    from .runner import Stage

    return [
        Stage("load", load_stage),
        Stage("normalize", normalize_stage),
        Stage("aggregate", aggregate_stage),
        Stage("report", report_stage),
        Stage("audit", audit_stage),
    ]
