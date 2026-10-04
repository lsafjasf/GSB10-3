"""失败隔离用例：第三方模块抛任何异常都必须被转成 Err，绝不冒泡到主流程。"""

import unittest

import _bootstrap  # noqa: F401

from guard import guard_call
from pipeline import run_batch
from result import Err, Ok
from third_party import pricing


def _order(coupon=None, sku="SKU-1"):
    order = {"order_id": "ISO-1", "items": [{"sku": sku, "qty": 2, "unit_price": 9.99}]}
    if coupon is not None:
        order["coupon"] = coupon
    return order


class TestGuardCall(unittest.TestCase):
    def test_success_wrapped_in_ok(self):
        result = guard_call("pricing", lambda: 42)
        self.assertIsInstance(result, Ok)
        self.assertEqual(result.value, 42)

    def test_arbitrary_exception_becomes_err(self):
        def boom():
            raise ValueError("nope")

        result = guard_call("pricing", boom)
        self.assertIsInstance(result, Err)
        self.assertEqual(result.problems[0].stage, "pricing")
        self.assertIn("ValueError", result.problems[0].reason)
        self.assertIn("nope", result.problems[0].reason)

    def test_no_exception_leaks(self):
        for exc in (KeyError("k"), TypeError("t"), ZeroDivisionError("z"),
                    ConnectionError("c"), RuntimeError("r")):
            def raiser(e=exc):
                raise e
            result = guard_call("pricing", raiser)
            self.assertIsInstance(result, Err, "%r leaked" % exc)


class TestPricingIsolation(unittest.TestCase):
    """第三方模块真实抛出的各类异常，在 run_batch 中都必须被隔离。"""

    def _run(self, orders, tmp_path):
        out = tmp_path / "out.jsonl"
        outcome = run_batch({"orders": orders}, str(out))
        return outcome, out

    def test_unknown_coupon_isolated(self):
        orders = [_order(coupon="NOT_A_COUPON")]
        outcome, out = self._run_tmp(orders)
        self.assertEqual(outcome.records, [])
        self.assertEqual(len(outcome.failures), 1)
        self.assertEqual(outcome.failures[0].stage, "pricing")
        self.assertIn("orders[0]", outcome.failures[0].path)
        self.assertIn("CouponError", outcome.failures[0].reason)
        self.assertIsNone(outcome.output_path)
        self.assertFalse(out.exists(), "全部失败时不应产生输出文件")

    def test_unknown_sku_isolated(self):
        outcome, _ = self._run_tmp([_order(sku="UNKNOWN-9")])
        self.assertEqual(outcome.records, [])
        self.assertEqual(len(outcome.failures), 1)
        self.assertIn("SkuLookupError", outcome.failures[0].reason)

    def test_connection_error_isolated(self):
        def flaky(items, coupon=None):
            raise ConnectionError("pricing service unreachable")

        original = pricing.quote
        pricing.quote = flaky
        try:
            outcome, _ = self._run_tmp([_order()])
        finally:
            pricing.quote = original
        self.assertEqual(outcome.records, [])
        self.assertEqual(len(outcome.failures), 1)
        self.assertIn("ConnectionError", outcome.failures[0].reason)

    def test_partial_failure_does_not_block_others(self):
        orders = [_order(), _order(coupon="BOOM"), _order()]
        for idx, order in enumerate(orders):
            order["order_id"] = "ISO-%d" % idx
        outcome, out = self._run_tmp(orders)
        self.assertEqual([r["order_id"] for r in outcome.records], ["ISO-0", "ISO-2"])
        self.assertEqual(len(outcome.failures), 1)
        self.assertIn("orders[1]", outcome.failures[0].path)
        self.assertTrue(out.exists())
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2, "半成品数据不得出现：只写成功的两条")

    def _run_tmp(self, orders):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return self._run(orders, Path(tmp.name))


if __name__ == "__main__":
    unittest.main()
