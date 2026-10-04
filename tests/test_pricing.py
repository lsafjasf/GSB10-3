"""回归测试：表驱动重构版 vs 原始嵌套实现。

覆盖：
1. 全部 2592 个条件组合逐一对拍（含两边都会抛错的组合）；
2. 金额边界取值（0 / 免邮阈值 500 两侧 / 负数 / 极值浮点）；
3. 未知枚举值、None 金额等异常输入；
4. 不可达组合的行为一致性（如 overseas+express 恒为 RuntimeError）。

运行：python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import legacy_pricing
import pricing
from enumerate_table import all_combinations


def outcome(func, order):
    """把返回值和异常统一成可比较的描述。"""
    try:
        return ("OK", func(order))
    except Exception as exc:
        return ("ERR", type(exc).__name__, str(exc))


def make_order(**overrides):
    order = {
        "amount": 50.0,
        "user_level": "normal",
        "region": "domestic",
        "category": "normal",
        "express": False,
        "holiday": False,
        "account_status": "active",
    }
    order.update(overrides)
    return order


class TestFullCombinationDiff(unittest.TestCase):
    """全部条件组合对拍：重构版必须与原始实现逐组合一致。"""

    def test_all_combinations_match(self):
        mismatches = []
        for combo in all_combinations():
            old = outcome(legacy_pricing.quote, dict(combo))
            new = outcome(pricing.quote, dict(combo))
            if old != new:
                mismatches.append((combo, old, new))
        self.assertEqual([], mismatches,
                         "%d combinations differ, first: %r" % (len(mismatches), mismatches[:1]))


class TestBoundaryAmounts(unittest.TestCase):
    """金额边界：0、免邮阈值 500 两侧、负数、浮点极值，在多种上下文下对拍。"""

    BOUNDARIES = [-1e308, -0.01, -1e-9, 0.0, 1e-9, 0.01,
                  499.99, 499.999999, 500.0, 500.000001, 1e308]

    CONTEXTS = [
        {},
        {"express": True, "holiday": True},
        {"region": "remote", "category": "fragile"},
        {"region": "overseas", "category": "fragile", "user_level": "gold"},
        {"user_level": "staff", "holiday": True},
        {"account_status": "frozen"},
        {"account_status": "banned"},
    ]

    def test_boundary_amounts_match(self):
        for amount in self.BOUNDARIES:
            for ctx in self.CONTEXTS:
                order = make_order(amount=amount, **ctx)
                old = outcome(legacy_pricing.quote, order)
                new = outcome(pricing.quote, order)
                self.assertEqual(old, new, "amount=%r ctx=%r" % (amount, ctx))

    def test_free_shipping_threshold_exact(self):
        # 499.99 不免邮，500.00 免邮（domestic、非加急）
        self.assertEqual(("OK", 6.0), outcome(pricing.quote, make_order(amount=499.99)))
        self.assertEqual(("OK", 0.0), outcome(pricing.quote, make_order(amount=500.0)))

    def test_zero_amount_short_circuits_banned(self):
        # 原实现行为：amount==0 在封号检查之前返回 0.0（疑似遗漏，见 docs/UNREACHABLE.md G1）
        self.assertEqual(("OK", 0.0),
                         outcome(pricing.quote, make_order(amount=0.0, account_status="banned")))


class TestErrorCombinations(unittest.TestCase):
    """原实现会抛错的组合：异常类型与消息都要一致。"""

    CASES = [
        ({"amount": -1.0}, ValueError),
        ({"account_status": "banned"}, PermissionError),
        ({"account_status": "banned", "amount": -1.0}, ValueError),  # 金额检查优先于封号
        ({"account_status": "frozen", "region": "overseas"}, PermissionError),
        ({"account_status": "frozen", "express": True}, RuntimeError),
        ({"account_status": "frozen", "region": "overseas", "express": True}, PermissionError),
        ({"region": "remote", "category": "frozen"}, ValueError),
        ({"region": "overseas", "category": "frozen"}, ValueError),
        ({"region": "overseas", "express": True}, RuntimeError),
        ({"region": "overseas", "category": "frozen", "express": True}, ValueError),  # 冷冻检查优先
        ({"region": "moon"}, ValueError),
        ({"category": "liquid"}, ValueError),
        ({"account_status": "locked"}, ValueError),
    ]

    def test_error_cases_match(self):
        for overrides, exc_type in self.CASES:
            order = make_order(**overrides)
            old = outcome(legacy_pricing.quote, order)
            new = outcome(pricing.quote, order)
            self.assertEqual("ERR", new[0], "%r should raise" % (overrides,))
            self.assertEqual(exc_type.__name__, new[1], "%r" % (overrides,))
            self.assertEqual(old, new, "%r" % (overrides,))

    def test_none_amount_raises_typeerror_on_both(self):
        order = make_order(amount=None)
        self.assertEqual(outcome(legacy_pricing.quote, order),
                         outcome(pricing.quote, order))
        self.assertEqual("ERR", outcome(pricing.quote, order)[0])

    def test_unknown_user_level_gets_no_discount_on_both(self):
        order = make_order(user_level="platinum")
        self.assertEqual(outcome(legacy_pricing.quote, order),
                         outcome(pricing.quote, order))


class TestUnreachableCombinations(unittest.TestCase):
    """不可达/冗余组合的行为固定性（详见 docs/UNREACHABLE.md）。"""

    def test_overseas_express_always_runtime_error(self):
        for level in ("normal", "silver", "gold", "staff"):
            order = make_order(region="overseas", category="normal", express=True,
                               user_level=level)
            self.assertEqual("RuntimeError", outcome(pricing.quote, order)[1])

    def test_frozen_account_express_always_runtime_error(self):
        order = make_order(account_status="frozen", express=True, holiday=True)
        self.assertEqual("RuntimeError", outcome(pricing.quote, order)[1])

    def test_silver_overseas_gets_no_discount(self):
        # 疑似遗漏：silver 仅 domestic 打折；行为保留并标注
        self.assertEqual(("OK", 40.0),
                         outcome(pricing.quote,
                                 make_order(region="overseas", user_level="silver")))


if __name__ == "__main__":
    unittest.main()
