"""边界校验用例：合法输入、字段缺失、类型错误、未知字段、边界值。"""

import json
import os
import unittest

import _bootstrap  # noqa: F401

from result import Err, Ok
from validation import validate_orders

ROOT = _bootstrap.ROOT


def _valid_order():
    return {"order_id": "A-1", "items": [{"sku": "SKU-1", "qty": 2, "unit_price": 9.99}]}


class TestValidInput(unittest.TestCase):
    def test_minimal_valid_order(self):
        result = validate_orders({"orders": [_valid_order()]})
        self.assertIsInstance(result, Ok)
        self.assertEqual(result.value["orders"][0]["order_id"], "A-1")

    def test_optional_coupon_and_null_coupon(self):
        with_coupon = dict(_valid_order(), coupon="SAVE10")
        null_coupon = dict(_valid_order(), coupon=None)
        result = validate_orders({"orders": [with_coupon, null_coupon]})
        self.assertIsInstance(result, Ok)

    def test_boundary_values_accepted(self):
        order = {
            "order_id": "E" * 32,  # 长度上限
            "items": [{"sku": "x", "qty": 1, "unit_price": 0}],  # 最小合法值
        }
        self.assertIsInstance(validate_orders({"orders": [order]}), Ok)

    def test_validation_does_not_mutate_payload(self):
        payload = {"orders": [_valid_order()]}
        snapshot = json.dumps(payload, sort_keys=True)
        validate_orders(payload)
        self.assertEqual(json.dumps(payload, sort_keys=True), snapshot)


class TestMissingFields(unittest.TestCase):
    def test_missing_items(self):
        result = validate_orders({"orders": [{"order_id": "X-1"}]})
        self.assertIsInstance(result, Err)
        self.assertTrue(any("items" in p.path for p in result.problems))

    def test_missing_order_id(self):
        order = {"items": [{"sku": "SKU-1", "qty": 1, "unit_price": 1}]}
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)
        self.assertTrue(any("order_id" in p.path for p in result.problems))

    def test_missing_item_unit_price(self):
        order = {"order_id": "X-1", "items": [{"sku": "SKU-1", "qty": 1}]}
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)
        self.assertTrue(any("unit_price" in p.path for p in result.problems))

    def test_missing_top_level_orders(self):
        result = validate_orders({})
        self.assertIsInstance(result, Err)
        self.assertTrue(any(p.path == "$.orders" for p in result.problems))


class TestTypeErrors(unittest.TestCase):
    def test_qty_string(self):
        order = _valid_order()
        order["items"][0]["qty"] = "2"
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)
        problem = result.problems[0]
        self.assertEqual(problem.path, "orders[0].items[0].qty")
        self.assertIn("整数", problem.reason)

    def test_qty_bool_is_rejected(self):
        order = _valid_order()
        order["items"][0]["qty"] = True
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)
        self.assertIn("bool", result.problems[0].reason)

    def test_unit_price_string(self):
        order = _valid_order()
        order["items"][0]["unit_price"] = "9.99"
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)
        self.assertIn("unit_price", result.problems[0].path)

    def test_orders_not_a_list(self):
        result = validate_orders({"orders": {"order_id": "X-1"}})
        self.assertIsInstance(result, Err)

    def test_top_level_not_object(self):
        result = validate_orders([1, 2, 3])
        self.assertIsInstance(result, Err)
        self.assertEqual(result.problems[0].path, "$")

    def test_coupon_wrong_type(self):
        order = dict(_valid_order(), coupon=10)
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)
        self.assertIn("coupon", result.problems[0].path)


class TestValueAndShapeRules(unittest.TestCase):
    def test_qty_zero_and_negative(self):
        for bad in (0, -3):
            order = _valid_order()
            order["items"][0]["qty"] = bad
            result = validate_orders({"orders": [order]})
            self.assertIsInstance(result, Err, "qty=%r should fail" % bad)
            self.assertIn("正整数", result.problems[0].reason)

    def test_negative_unit_price(self):
        order = _valid_order()
        order["items"][0]["unit_price"] = -0.01
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)

    def test_bad_order_id_charset_and_length(self):
        for bad in ("has space", "bad_id!", "X" * 33, ""):
            order = dict(_valid_order(), order_id=bad)
            result = validate_orders({"orders": [order]})
            self.assertIsInstance(result, Err, "order_id=%r should fail" % bad)

    def test_empty_items(self):
        order = dict(_valid_order(), items=[])
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)

    def test_unknown_fields_rejected(self):
        order = dict(_valid_order(), discount_code="SAVE10")
        result = validate_orders({"orders": [order]})
        self.assertIsInstance(result, Err)
        self.assertIn("未知字段", result.problems[0].reason)

    def test_multiple_problems_reported_together(self):
        payload = {"orders": [{"items": [{"qty": "x"}]}]}
        result = validate_orders(payload)
        self.assertIsInstance(result, Err)
        self.assertGreaterEqual(len(result.problems), 3)

    def test_error_path_points_to_exact_order(self):
        payload = {"orders": [_valid_order(), _valid_order(), {"order_id": "X"}]}
        result = validate_orders(payload)
        self.assertIsInstance(result, Err)
        self.assertTrue(all(p.path.startswith("orders[2]") for p in result.problems))


class TestInvalidSamplesFile(unittest.TestCase):
    """data/invalid_samples.json 中的每个样本都必须被拒绝。"""

    def test_all_samples_rejected(self):
        path = os.path.join(ROOT, "data", "invalid_samples.json")
        with open(path, encoding="utf-8") as fh:
            samples = json.load(fh)
        for name, payload in samples.items():
            with self.subTest(sample=name):
                self.assertIsInstance(validate_orders(payload), Err)


if __name__ == "__main__":
    unittest.main()
