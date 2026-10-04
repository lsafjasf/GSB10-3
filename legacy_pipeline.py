"""重构前的遗留实现：一段串行的同步处理。

特点（即本次重构要解决的问题）：
- 解析、校验、换算、汇总、落盘全部糊在一个函数里，中间结果不留痕；
- 阶段之间没有边界，无法挂钩子观测；
- 任何阶段失败都只能从头重跑，副作用（report/outbox 落盘）无法幂等；
- 出了问题只能翻日志猜。

输出契约（重构后必须逐字节一致）：
- report.txt: 先按输入顺序输出每行 ORDER，再按输入顺序输出 REJECT，最后 SUMMARY；
- outbox.log: 每个受理订单一行 NOTIFY。
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

RATES = {
    "USD": Decimal("7.10"),
    "EUR": Decimal("7.80"),
    "CNY": Decimal("1.00"),
}
CENT = Decimal("0.01")
FEE_RATE = Decimal("0.01")


def process_orders(lines):
    """处理一批订单 CSV 文本行，返回 (report_text, outbox_text)。"""
    accepted = []
    rejected = []
    seen = set()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            rejected.append((parts[0] if parts and parts[0] else "?", "bad_row"))
            continue

        order_id, customer, amount_s, currency = parts
        currency = currency.upper()

        try:
            amount = Decimal(amount_s)
        except InvalidOperation:
            rejected.append((order_id or "?", "bad_amount"))
            continue

        if not order_id:
            rejected.append(("?", "missing_id"))
            continue
        if not customer:
            rejected.append((order_id, "missing_customer"))
            continue
        if amount <= 0:
            rejected.append((order_id, "amount_not_positive"))
            continue
        if order_id in seen:
            rejected.append((order_id, "duplicate_id"))
            continue
        seen.add(order_id)

        rate = RATES.get(currency)
        if rate is None:
            rejected.append((order_id, "unknown_currency"))
            continue

        cny = (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        fee = (cny * FEE_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
        total = cny + fee
        accepted.append((order_id, customer, total))

    report_lines = [
        f"ORDER {order_id} {customer} total={total} CNY"
        for order_id, customer, total in accepted
    ]
    report_lines += [
        f"REJECT {order_id} {reason}" for order_id, reason in rejected
    ]
    total_sum = sum((total for _, _, total in accepted), Decimal("0.00"))
    report_lines.append(
        f"SUMMARY accepted={len(accepted)} rejected={len(rejected)} "
        f"total={total_sum} CNY"
    )

    outbox_lines = [
        f"NOTIFY {order_id} {total}" for order_id, _, total in accepted
    ]

    report_text = "\n".join(report_lines) + "\n"
    outbox_text = "\n".join(outbox_lines) + ("\n" if outbox_lines else "")
    return report_text, outbox_text


def process_file(input_path, out_dir):
    with open(input_path, "r", encoding="utf-8") as fh:
        report_text, outbox_text = process_orders(fh.readlines())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(report_text)
    with open(os.path.join(out_dir, "outbox.log"), "w", encoding="utf-8") as fh:
        fh.write(outbox_text)
    return report_text, outbox_text
