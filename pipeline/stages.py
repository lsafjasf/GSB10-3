"""订单处理流水线的阶段定义。

阶段划分（与遗留函数一一对应，中间结果用 dataclass 显式传递）：
  parse        原始文本行 -> ParseResult(rows, rejects)
  validate     ParseResult -> ValidateResult(rows, rejects)
  enrich       ValidateResult -> EnrichResult(orders, rejects)   汇率换算+手续费
  aggregate    EnrichResult -> Report(accepted, rejects, total)  汇总，rejects 按行号重排
  emit_report  Report -> 副作用键 report:* / reject:* / summary
  emit_notify  Report -> 副作用键 notify:*（可被 when 条件跳过）

只有 emit_* 两个阶段产生副作用，且全部经由 EffectSink 的幂等键。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

RATES = {
    "USD": Decimal("7.10"),
    "EUR": Decimal("7.80"),
    "CNY": Decimal("1.00"),
}
CENT = Decimal("0.01")
FEE_RATE = Decimal("0.01")


@dataclass
class Row:
    order_id: str
    customer: str
    amount: Decimal
    currency: str
    line_no: int


@dataclass
class Reject:
    order_id: str
    reason: str
    line_no: int


@dataclass
class Order:
    order_id: str
    customer: str
    total_cny: Decimal
    line_no: int


@dataclass
class ParseResult:
    rows: list = field(default_factory=list)
    rejects: list = field(default_factory=list)


@dataclass
class ValidateResult:
    rows: list = field(default_factory=list)
    rejects: list = field(default_factory=list)


@dataclass
class EnrichResult:
    orders: list = field(default_factory=list)
    rejects: list = field(default_factory=list)


@dataclass
class Report:
    accepted: list = field(default_factory=list)
    rejects: list = field(default_factory=list)
    total_cny: Decimal = Decimal("0.00")


@dataclass
class EmitResult:
    applied_keys: list = field(default_factory=list)


def parse_stage(ctx):
    """文本行 -> 结构化 Row；坏行/坏金额在此拒绝。"""
    result = ParseResult()
    for line_no, raw in enumerate(ctx.input, 1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            order_id = parts[0] if parts and parts[0] else "?"
            result.rejects.append(Reject(order_id, "bad_row", line_no))
            continue
        order_id, customer, amount_s, currency = parts
        try:
            amount = Decimal(amount_s)
        except InvalidOperation:
            result.rejects.append(Reject(order_id or "?", "bad_amount", line_no))
            continue
        result.rows.append(Row(order_id, customer, amount, currency.upper(), line_no))
    return result


def validate_stage(ctx):
    """业务校验：缺字段、非正金额、重复订单号。"""
    parsed: ParseResult = ctx.results["parse"]
    result = ValidateResult(rejects=list(parsed.rejects))
    seen = set()
    for row in parsed.rows:
        if not row.order_id:
            result.rejects.append(Reject("?", "missing_id", row.line_no))
        elif not row.customer:
            result.rejects.append(Reject(row.order_id, "missing_customer", row.line_no))
        elif row.amount <= 0:
            result.rejects.append(Reject(row.order_id, "amount_not_positive", row.line_no))
        elif row.order_id in seen:
            result.rejects.append(Reject(row.order_id, "duplicate_id", row.line_no))
        else:
            seen.add(row.order_id)
            result.rows.append(row)
    return result


def enrich_stage(ctx):
    """汇率换算 + 手续费；未知币种在此拒绝。"""
    validated: ValidateResult = ctx.results["validate"]
    result = EnrichResult(rejects=list(validated.rejects))
    for row in validated.rows:
        rate = RATES.get(row.currency)
        if rate is None:
            result.rejects.append(Reject(row.order_id, "unknown_currency", row.line_no))
            continue
        cny = (row.amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        fee = (cny * FEE_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
        result.orders.append(Order(row.order_id, row.customer, cny + fee, row.line_no))
    return result


def aggregate_stage(ctx):
    """汇总为 Report；rejects 按原始行号排序，保证与遗留实现的输出顺序一致。"""
    enriched: EnrichResult = ctx.results["enrich"]
    rejects = sorted(enriched.rejects, key=lambda r: r.line_no)
    total = sum((o.total_cny for o in enriched.orders), Decimal("0.00"))
    return Report(list(enriched.orders), rejects, total)


def emit_report_stage(ctx):
    """副作用：报表行 + 汇总行。幂等键 report:{id} / reject:{line_no} / summary。"""
    report: Report = ctx.results["aggregate"]
    sink = ctx.sink
    applied = []
    for order in report.accepted:
        key = f"report:{order.order_id}"
        sink.apply(key, f"ORDER {order.order_id} {order.customer} "
                        f"total={order.total_cny} CNY")
        applied.append(key)
    for rej in report.rejects:
        key = f"reject:{rej.line_no}:{rej.order_id}"
        sink.apply(key, f"REJECT {rej.order_id} {rej.reason}")
        applied.append(key)
    sink.apply("summary", f"SUMMARY accepted={len(report.accepted)} "
                          f"rejected={len(report.rejects)} "
                          f"total={report.total_cny} CNY")
    applied.append("summary")
    return EmitResult(applied)


def emit_notify_stage(ctx):
    """副作用：外发通知。幂等键 notify:{id}。可被 when 条件整体跳过。"""
    report: Report = ctx.results["aggregate"]
    sink = ctx.sink
    applied = []
    for order in report.accepted:
        key = f"notify:{order.order_id}"
        sink.apply(key, f"NOTIFY {order.order_id} {order.total_cny}")
        applied.append(key)
    return EmitResult(applied)
