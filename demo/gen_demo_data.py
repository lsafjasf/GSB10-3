"""生成模拟回归的失败用例数据（确定性输出 demo/failures.json，共 33 条）。

场景设计（覆盖题目要求的全部情形）：
- 26 条：支付网关回归同一根因
    22 条 AssertionError "expected 200 got 500 (order N)" @ gateway.charge:88
     4 条 AssertionError "expected status 200 but got 500 (order N)"
         @ 同一位置（不同断言工具的措辞，靠软合并归入同簇）
     —— 演示“单一大根因 + 信息措辞漂移”
- 3 条：库存扣减 KeyError 'sku' @ inventory.service.deduct:55（独立根因）
- 1 条：CSV 导出 UnicodeDecodeError（孤立失败，只有一条）
- 3 条噪声：setup 夹具失败、teardown 超时、调用阶段网络超时（关键失败
  信息恰好也是 500，用于验证关键/噪声不混簇）
"""

import json
from pathlib import Path

OUT = Path(__file__).with_name("failures.json")

GATEWAY = [
    {"file": "/home/ci/workspace/src/payment/gateway.py", "function": "charge", "line": 88},
]
INVENTORY = [
    {"file": "src/inventory/service.py", "function": "deduct", "line": 55},
]
EXPORT = [
    {"file": "src/report/exporter.py", "function": "to_csv", "line": 34},
]

TEST_MODULES = [
    "tests/payment/test_checkout.py::test_checkout_basic",
    "tests/payment/test_checkout.py::test_checkout_with_coupon",
    "tests/payment/test_refund.py::test_refund_full",
    "tests/payment/test_subscription.py::test_renew_monthly",
    "tests/payment/test_subscription.py::test_renew_yearly",
    "tests/payment/test_wallet.py::test_pay_from_wallet",
]


def _test_frame(test: str, line: int):
    module = test.split("::")[0]
    func = test.split("::")[1]
    return {"file": module, "function": func, "line": line}


def main() -> None:
    cases = []

    # 22 条主力失败（信息完全相同的骨架，仅 order 号不同）
    for i in range(1, 23):
        test = TEST_MODULES[i % len(TEST_MODULES)]
        cases.append({
            "case_id": f"TC-{i:03d}",
            "test": test,
            "error_type": "AssertionError",
            "message": f"expected 200 got 500 (order {1000 + i})",
            "phase": "call",
            "traceback": [
                _test_frame(test, 40 + i),
                {"file": "src/payment/service.py", "function": "checkout", "line": 120},
                GATEWAY[0],
            ],
        })

    # 4 条措辞漂移失败（同位置同类型，模板高相似 -> 软合并）
    for i in range(23, 27):
        test = TEST_MODULES[(i + 2) % len(TEST_MODULES)]
        cases.append({
            "case_id": f"TC-{i:03d}",
            "test": test,
            "error_type": "AssertionError",
            "message": f"expected status 200 but got 500 (order {1000 + i})",
            "phase": "call",
            "traceback": [
                _test_frame(test, 80 + i),
                {"file": "src/payment/service.py", "function": "checkout", "line": 120},
                GATEWAY[0],
            ],
        })

    # 3 条独立根因：库存
    for i, test in enumerate([
        "tests/inventory/test_stock.py::test_deduct_basic",
        "tests/inventory/test_stock.py::test_deduct_batch",
        "tests/inventory/test_order.py::test_order_locks_stock",
    ], start=27):
        cases.append({
            "case_id": f"TC-{i:03d}",
            "test": test,
            "error_type": "KeyError",
            "message": "'sku'",
            "phase": "call",
            "traceback": [_test_frame(test, 15 + i), INVENTORY[0]],
        })

    # 1 条孤立失败：CSV 导出（只有一条）
    cases.append({
        "case_id": "TC-030",
        "test": "tests/report/test_export.py::test_export_gbk_csv",
        "error_type": "UnicodeDecodeError",
        "message": "'gbk' codec can't decode byte 0xff in position 12: illegal multibyte sequence",
        "phase": "call",
        "traceback": [_test_frame("tests/report/test_export.py::test_export_gbk_csv", 21), EXPORT[0]],
    })

    # 3 条噪声
    cases.append({
        "case_id": "TC-031",
        "test": "tests/test_orders.py::test_create_order",
        "error_type": "RuntimeError",
        "message": "db container not ready after 30s",
        "phase": "setup",
        "traceback": [
            {"file": "tests/conftest.py", "function": "db_fixture", "line": 12},
        ],
    })
    cases.append({
        "case_id": "TC-032",
        "test": "tests/payment/test_checkout.py::test_checkout_concurrent",
        "error_type": "TimeoutError",
        "message": "failed to release redis lock: timed out after 10s",
        "phase": "teardown",
        "traceback": [
            {"file": "tests/conftest.py", "function": "redis_lock_fixture", "line": 58},
        ],
    })
    cases.append({
        "case_id": "TC-033",
        "test": "tests/integration/test_smoke.py::test_homepage",
        "error_type": "TimeoutError",
        "message": "request to http://10.0.0.8:8080/api/health timed out after 30s",
        "phase": "call",
        "traceback": [
            {"file": "src/utils/http.py", "function": "post", "line": 40},
            {"file": "/usr/lib/python3.12/socket.py", "function": "create_connection", "line": 845},
        ],
    })

    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases -> {OUT}")


if __name__ == "__main__":
    main()
