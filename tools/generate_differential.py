"""生成对拍数据：合法输入 + 第三方模块（重构基准）的期望输出。

用法：python3 tools/generate_differential.py
输出：data/differential/case_XX.json，内容为 {"input": ..., "expected_report": ...}
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import legacy_third_party as legacy

CASES = [
    # 常规单条目
    {
        "id": "ORD-001",
        "currency": "CNY",
        "items": [{"name": "键盘", "qty": 2, "price": 199.5}],
    },
    # 多条目 + 折扣
    {
        "id": "ORD-002",
        "currency": "USD",
        "discount": 0.1,
        "items": [
            {"name": "mouse", "qty": 3, "price": 25.0},
            {"name": "cable", "qty": 5, "price": 4.2},
        ],
    },
    # 整数数量与价格、零折扣显式给出
    {
        "id": "ORD-003",
        "currency": "EUR",
        "discount": 0,
        "items": [{"name": "laptop", "qty": 1, "price": 1299}],
    },
    # 边界：全额折扣
    {
        "id": "ORD-004",
        "currency": "CNY",
        "discount": 1.0,
        "items": [{"name": "服务包", "qty": 10, "price": 88.8}],
    },
    # 边界：价格为 0 的赠品条目
    {
        "id": "ORD-005",
        "currency": "USD",
        "items": [
            {"name": "gift", "qty": 1, "price": 0},
            {"name": "book", "qty": 4, "price": 12.75},
        ],
    },
    # 浮点数量（如按重量计费）
    {
        "id": "ORD-006",
        "currency": "EUR",
        "items": [{"name": "apple", "qty": 2.5, "price": 3.6}],
    },
]


def main() -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "differential")
    os.makedirs(out_dir, exist_ok=True)
    for idx, record in enumerate(CASES, start=1):
        expected = legacy.compute_report(record)
        payload = {"input": record, "expected_report": expected}
        path = os.path.join(out_dir, f"case_{idx:02d}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"written {path}")


if __name__ == "__main__":
    main()
