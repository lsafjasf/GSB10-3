"""管线行为：非法输入不进核心逻辑、不写半成品；原子落盘。"""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from pipeline import InputRejected, run_batch
from third_party import pricing


class TestBoundaryRejectsBeforeCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out.jsonl"

    def test_invalid_input_raises_inputrejected(self):
        bad = {"orders": [{"order_id": "X-1"}]}  # 缺 items
        with self.assertRaises(InputRejected) as ctx:
            run_batch(bad, str(self.out))
        self.assertTrue(ctx.exception.problems)
        self.assertTrue(all(p.stage == "validation" for p in ctx.exception.problems))
        self.assertIn("items", str(ctx.exception))

    def test_invalid_input_does_not_touch_output(self):
        self.out.write_text("KEEP-ME\n", encoding="utf-8")
        with self.assertRaises(InputRejected):
            run_batch({"orders": [{"order_id": "X", "items": []}]}, str(self.out))
        self.assertEqual(self.out.read_text(encoding="utf-8"), "KEEP-ME\n")

    def test_core_logic_never_entered_for_invalid_input(self):
        calls = []

        def spy(items, coupon=None):
            calls.append((items, coupon))
            return 0.0

        original = pricing.quote
        pricing.quote = spy
        try:
            with self.assertRaises(InputRejected):
                run_batch({"orders": [{"order_id": "X", "items": [
                    {"sku": "S", "qty": -1, "unit_price": 1}]}]}, str(self.out))
        finally:
            pricing.quote = original
        self.assertEqual(calls, [], "非法输入在进入第三方计价之前就应被拒绝")

    def test_partial_failure_writes_only_successful_records(self):
        orders = {
            "orders": [
                {"order_id": "OK-1", "items": [{"sku": "S", "qty": 1, "unit_price": 5}]},
                {"order_id": "BAD-1", "items": [{"sku": "S", "qty": 1, "unit_price": 5}],
                 "coupon": "NOPE"},
                {"order_id": "OK-2", "items": [{"sku": "S", "qty": 2, "unit_price": 5}]},
            ]
        }
        outcome = run_batch(orders, str(self.out))
        self.assertEqual([r["order_id"] for r in outcome.records], ["OK-1", "OK-2"])
        self.assertEqual(len(outcome.failures), 1)
        lines = self.out.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn("BAD-1", self.out.read_text(encoding="utf-8"))

    def test_all_failing_writes_nothing(self):
        orders = {"orders": [
            {"order_id": "BAD-1", "items": [{"sku": "S", "qty": 1, "unit_price": 5}],
             "coupon": "NOPE"},
        ]}
        outcome = run_batch(orders, str(self.out))
        self.assertEqual(outcome.records, [])
        self.assertFalse(self.out.exists())
        self.assertIsNone(outcome.output_path)


if __name__ == "__main__":
    unittest.main()
