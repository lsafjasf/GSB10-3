"""重构后的订单批处理。

与 legacy_pipeline 的差异（行为契约）：
    1. 边界校验：run_batch 先 validate_orders，非法输入整体拒绝（InputRejected），
       不进入核心逻辑、不写任何输出；
    2. 失败隔离：第三方 pricing 的异常经 guard_call 收敛为 Err，转为该订单的
       失败记录，绝不冒泡到主流程；
    3. 原子落盘：全部处理完才一次性写文件（临时文件 + os.replace），
       不存在半成品输出；
    4. 合法输入的计算结果与 legacy 完全一致（见 tests/test_parity.py）。
"""

import json
import os
import tempfile

from guard import guard_call
from result import BatchOutcome, Ok, Problem
from third_party import pricing
from validation import validate_orders


class InputRejected(Exception):
    """输入未通过边界校验。problems 属性携带全部可读原因。"""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(str(p) for p in self.problems))


def _price_order(order):
    """返回 Ok(record) 或 Err([Problem])。核心计算与 legacy 保持一致。"""
    items = order["items"]
    subtotal = round(sum(i["qty"] * i["unit_price"] for i in items), 2)
    priced = guard_call("pricing", pricing.quote, items, order.get("coupon"))
    if not priced.is_ok:
        return priced
    discount = priced.value
    total = round(subtotal - discount, 2)
    tax = round(total * pricing.TAX_RATE, 2)
    return Ok({
        "order_id": order["order_id"],
        "subtotal": subtotal,
        "discount": round(discount, 2),
        "total": total,
        "tax": tax,
    })


def _atomic_write_jsonl(records, out_path):
    """先写临时文件再 os.replace，保证输出要么完整要么不存在。"""
    if not records:
        return None
    directory = os.path.dirname(os.path.abspath(out_path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return out_path


def run_batch(payload, out_path):
    """处理一批订单。

    非法输入：抛出 InputRejected（在进入核心逻辑之前），不产生任何输出文件。
    合法输入：返回 BatchOutcome；单订单失败只记入 failures，不影响其他订单。
    """
    checked = validate_orders(payload)
    if not checked.is_ok:
        raise InputRejected(checked.problems)

    records = []
    failures = []
    for idx, order in enumerate(payload["orders"]):
        result = _price_order(order)
        if result.is_ok:
            records.append(result.value)
        else:
            for problem in result.problems:
                failures.append(Problem(problem.stage,
                                        "orders[%d]" % idx,
                                        problem.reason))
    written = _atomic_write_jsonl(records, out_path)
    return BatchOutcome(records=records, failures=failures, output_path=written)
