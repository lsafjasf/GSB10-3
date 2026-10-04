"""重构前的订单批处理（保留用于对拍，勿再修改）。

问题：
    1. 不做任何输入校验，非法输入直接进入核心逻辑；
    2. 第三方模块的异常直接冒泡到主流程；
    3. 边处理边写文件，中途失败会留下半成品输出。
"""

import json

from third_party import pricing


def _price_order(order):
    items = order["items"]
    subtotal = round(sum(i["qty"] * i["unit_price"] for i in items), 2)
    discount = pricing.quote(items, order.get("coupon"))
    total = round(subtotal - discount, 2)
    tax = round(total * pricing.TAX_RATE, 2)
    return {
        "order_id": order["order_id"],
        "subtotal": subtotal,
        "discount": round(discount, 2),
        "total": total,
        "tax": tax,
    }


def run_batch(orders, out_path):
    """逐条处理并立即落盘；任何异常直接冒泡，文件已是半成品。"""
    ok = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for order in orders:
            record = _price_order(order)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            ok += 1
    return ok
