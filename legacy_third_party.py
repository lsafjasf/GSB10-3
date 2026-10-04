"""模拟第三方模块（不可修改的遗留代码，作为对拍基准保留）。

两个已知问题：
1. 输入不合法时直接抛出 KeyError / TypeError / IndexError 等不稳定异常，
   调用方必须猜测异常类型；
2. process_and_write 边算边写，一旦中途崩溃，磁盘上留下半成品文件。
"""

import json

TAX_RATES = {"CNY": 0.13, "USD": 0.07, "EUR": 0.19}


def compute_report(record):
    """计算账单报告。纯计算函数：非法输入会抛任意异常。"""
    items = record["items"]
    lines = []
    subtotal = 0.0
    for item in items:
        qty = item["qty"]
        price = item["price"]
        amount = qty * price
        subtotal += amount
        lines.append({"name": item["name"], "amount": round(amount, 2)})

    tax = subtotal * TAX_RATES[record["currency"]]
    discount = subtotal * record.get("discount", 0.0)
    total = round(subtotal + tax - discount, 2)
    return {
        "id": record["id"],
        "lines": lines,
        "subtotal": round(subtotal, 2),
        "tax": round(tax, 2),
        "discount": round(discount, 2),
        "total": total,
    }


def process_and_write(record, out_path):
    """遗留写法：打开文件后逐条写入，中途异常会留下半成品文件。"""
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write('{"id": ')
        fh.write(json.dumps(record["id"]))
        fh.write(', "lines": [')
        for item in record["items"]:
            amount = item["qty"] * item["price"]
            fh.write(json.dumps({"name": item["name"], "amount": amount}))
            fh.write(", ")
        fh.write(']}')
