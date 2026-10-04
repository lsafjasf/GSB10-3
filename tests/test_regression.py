"""test_regression —— 表驱动重构的回归测试（仅标准库 unittest）。

覆盖：
  1. 全部 864 个条件组合代表值对拍；
  2. 每个组合内 amount/qty 段界 ±1 与中点的边界扫描；
  3. 原实现会抛错的非法输入（类型 / 越界 / 未知枚举 / 券门槛）；
  4. 文档化的历史怪癖（omission / 死分支）固化用例；
  5. 结构约束：pricing.settle 零 if、签名一致、legacy 嵌套 >=10、
     标签规则表无重叠无缺口、legacy 死语句零命中且其余语句全覆盖。
"""

import ast
import inspect
import itertools
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import build_decision_table as build  # noqa: E402
import enumerate as enum  # noqa: E402
import legacy_pricing  # noqa: E402
import pricing  # noqa: E402


def outcomes(args):
    try:
        old = legacy_pricing.settle(*args)
    except Exception as exc:  # noqa: BLE001
        old = ("error", "%s: %s" % (type(exc).__name__, exc))
    try:
        new = pricing.settle(*args)
    except Exception as exc:  # noqa: BLE001
        new = ("error", "%s: %s" % (type(exc).__name__, exc))
    return old, new


class TestAllCombos(unittest.TestCase):
    """全部条件组合代表值：新实现必须与原实现逐字节一致。"""

    def test_all_combos_match(self):
        combos = enum.all_combos()
        self.assertEqual(len(combos), 864)
        for combo in combos:
            args = enum.representative(combo)
            old, new = outcomes(args)
            self.assertEqual(old, new, "combo=%r args=%r" % (combo, args))

    def test_error_combos_documented(self):
        """cash 券最低 5000 分、gift 券最低 20000 分，低于门槛必须抛 ValueError。"""
        for combo in enum.all_combos():
            a_band, q_band, vip, channel, region, coupon, birthday = combo
            amount, qty, *_ = enum.representative(combo)
            if coupon == "cash" and a_band == 0:
                with self.assertRaises(ValueError):
                    legacy_pricing.settle(amount, qty, vip, channel, region, coupon, birthday)
            if coupon == "gift" and a_band in (0, 1, 2):
                with self.assertRaises(ValueError):
                    legacy_pricing.settle(amount, qty, vip, channel, region, coupon, birthday)


class TestBoundary(unittest.TestCase):
    """段界 ±1 / 中点逐点对拍（864 组合内全扫描）。"""

    def test_boundary_sweep(self):
        checked = 0
        for combo in enum.all_combos():
            a_band, q_band, vip, channel, region, coupon, birthday = combo
            for amount, qty in itertools.product(
                build.boundary_amounts(a_band), build.boundary_qtys(q_band)
            ):
                args = (amount, qty, vip, channel, region, coupon, birthday)
                old, new = outcomes(args)
                self.assertEqual(old, new, "args=%r" % (args,))
                checked += 1
        expected = 72 * sum(
            len(build.boundary_amounts(a)) * len(build.boundary_qtys(q))
            for a in range(4) for q in range(3))
        self.assertEqual(checked, expected)

    def test_threshold_boundaries(self):
        """关键阈值点：4999/5000、19999/20000、qty 9/10。"""
        cases = [
            (4999, 5, "normal", "app", "mainland", "cash", False),
            (5000, 5, "normal", "app", "mainland", "cash", False),
            (19999, 5, "normal", "app", "mainland", "gift", False),
            (20000, 5, "normal", "app", "mainland", "gift", False),
            (10000, 9, "gold", "app", "mainland", "none", False),
            (10000, 10, "gold", "app", "mainland", "none", False),
            (0, 1, "normal", "app", "mainland", "none", False),
        ]
        for args in cases:
            old, new = outcomes(args)
            self.assertEqual(old, new, "args=%r" % (args,))

    def test_fuzz(self):
        rng = random.Random(build.FUZZ_SEED)
        for _ in range(2000):
            args = (
                rng.randint(0, build.AMOUNT_CAP),
                rng.randint(1, build.QTY_CAP),
                rng.choice(enum.VIPS),
                rng.choice(enum.CHANNELS),
                rng.choice(enum.REGIONS),
                rng.choice(enum.COUPONS),
                rng.choice((False, True)),
            )
            old, new = outcomes(args)
            self.assertEqual(old, new, "args=%r" % (args,))


class TestValidationErrors(unittest.TestCase):
    """原实现会抛错的非法输入：错误类型与消息必须一致。"""

    INVALID = [
        (-1, 1, "gold", "app", "mainland", "none", False),
        (True, 1, "gold", "app", "mainland", "none", False),
        ("100", 1, "gold", "app", "mainland", "none", False),
        (100.5, 1, "gold", "app", "mainland", "none", False),
        (None, 1, "gold", "app", "mainland", "none", False),
        (1000, 0, "gold", "app", "mainland", "none", False),
        (1000, -3, "gold", "app", "mainland", "none", False),
        (1000, True, "gold", "app", "mainland", "none", False),
        (1000, "2", "gold", "app", "mainland", "none", False),
        (1000, 1, "platinum", "app", "mainland", "none", False),
        (1000, 1, "gold", "pos", "mainland", "none", False),
        (1000, 1, "gold", "app", "overseas", "none", False),
        (1000, 1, "gold", "app", "mainland", "voucher", False),
        (4999, 1, "gold", "app", "mainland", "cash", False),
        (19999, 1, "gold", "app", "mainland", "gift", False),
    ]

    def test_invalid_inputs_match(self):
        for args in self.INVALID:
            old, new = outcomes(args)
            self.assertEqual(old, new, "args=%r" % (args,))
            self.assertEqual(old[0], "error")

    def test_error_precedence(self):
        """类型错误先于金额越界；未知枚举先于券门槛。"""
        with self.assertRaises(TypeError):
            pricing.settle(-1.5, 0, "x", "x", "x", "x", False)
        with self.assertRaises(ValueError) as cm:
            pricing.settle(100, 1, "x", "app", "mainland", "cash", False)
        self.assertIn("vip", str(cm.exception))


class TestDocumentedQuirks(unittest.TestCase):
    """固化文档中记录的历史行为（防止"顺手修复"破坏下游报表）。"""

    def test_web_remote_shipping_omission(self):
        """web+remote 历史遗漏：运费静默为 0（omission，原样保留）。"""
        out = pricing.settle(500, 5, "normal", "web", "remote", "none", False)
        self.assertEqual(out["shipping"], 0)
        # 对照组：app+remote 同条件运费 1800，证明 0 是遗漏而非全局免费
        out_app = pricing.settle(500, 5, "normal", "app", "remote", "none", False)
        self.assertEqual(out_app["shipping"], 1800)

    def test_dead_label_never_returned(self):
        """冗余死分支里的标签永远不会出现在正常输出中。"""
        rng = random.Random(7)
        for _ in range(3000):
            args = (
                rng.randint(0, 60000), rng.randint(1, 30),
                rng.choice(enum.VIPS), rng.choice(enum.CHANNELS),
                rng.choice(enum.REGIONS), rng.choice(enum.COUPONS),
                rng.choice((False, True)),
            )
            try:
                out = pricing.settle(*args)
            except ValueError:
                continue
            self.assertNotIn(out["label"], ("gold-big-none-small", "unknown-vip"))

    def test_deepest_nested_label(self):
        """最深路径（11 层）标签：qty 10-19 与 >=20 分档。"""
        out = pricing.settle(25000, 12, "gold", "app", "remote", "cash", True)
        self.assertEqual(out["label"], "gold-big-cash-bday-remote-app-bulk")
        out_xl = pricing.settle(25000, 25, "gold", "app", "remote", "cash", True)
        self.assertEqual(out_xl["label"], "gold-big-cash-bday-remote-app-bulk-xl")

    def test_bulk_discounts(self):
        gold = pricing.settle(20000, 10, "gold", "app", "mainland", "none", False)
        silver = pricing.settle(20000, 10, "silver", "app", "mainland", "none", False)
        normal = pricing.settle(20000, 10, "normal", "app", "mainland", "none", False)
        self.assertEqual(gold["discount"], 20000 * 35 // 100)
        self.assertEqual(silver["discount"], 20000 * 14 // 100)
        self.assertEqual(normal["discount"], 20000 * 5 // 100)


class TestStructure(unittest.TestCase):
    """表驱动结构约束 + 规则表完备性 + legacy 覆盖证明。"""

    def test_settle_has_no_if(self):
        src = inspect.getsource(pricing.settle)
        tree = ast.parse(src)
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.If)], [])

    def test_signatures_identical(self):
        self.assertEqual(
            inspect.signature(pricing.settle),
            inspect.signature(legacy_pricing.settle),
        )

    def test_legacy_depth_at_least_ten(self):
        self.assertGreaterEqual(build.max_if_depth(), 10)

    def test_label_rules_cover_all_keys_uniquely(self):
        """864 个离散键每个必须命中且仅命中一条标签规则（无缺口、无重叠）。"""
        keys = list(itertools.product(
            enum.VIPS, range(4), enum.COUPONS, (False, True),
            enum.REGIONS, enum.CHANNELS, range(3),
        ))
        self.assertEqual(len(keys), 864)
        for key in keys:
            matched = [label for pattern, label in pricing.LABEL_RULES
                       if pricing._match(pattern, key)]
            self.assertEqual(len(matched), 1, "key=%r matched=%r" % (key, matched))

    def test_data_tables_complete(self):
        self.assertEqual(len(pricing.RATE_TABLE), 12)
        self.assertEqual(len(pricing.SHIPPING_TABLE), 4)
        self.assertEqual(len(pricing.FREE_SHIPPING), 3)
        self.assertEqual(set(pricing.CASH_OFF_TABLE),
                         {("cash", b) for b in range(4)})

    def test_legacy_dead_lines_never_hit_rest_covered(self):
        tracer = build.LineTracer(
            os.path.join(ROOT, "src", "legacy_pricing.py"))
        def call_safely(*args):
            try:
                legacy_pricing.settle(*args)
            except Exception:  # noqa: BLE001 - 抛错组合同样计入覆盖
                pass

        with tracer:
            for combo in enum.all_combos():
                call_safely(*enum.representative(combo))
            for combo in enum.all_combos():
                a_band, q_band, vip, channel, region, coupon, birthday = combo
                for amount, qty in itertools.product(
                    build.boundary_amounts(a_band), build.boundary_qtys(q_band)
                ):
                    call_safely(amount, qty, vip, channel, region, coupon, birthday)
            rng = random.Random(build.FUZZ_SEED)
            for _ in range(1000):
                call_safely(
                    rng.randint(0, build.AMOUNT_CAP),
                    rng.randint(1, build.QTY_CAP),
                    rng.choice(enum.VIPS), rng.choice(enum.CHANNELS),
                    rng.choice(enum.REGIONS), rng.choice(enum.COUPONS),
                    rng.choice((False, True)))
            for args in TestValidationErrors.INVALID:
                call_safely(*args)

        stmts = build.settle_statement_lines()
        marks = build.annotated_unreachable_lines()
        redundant = {l for l in marks if isinstance(l, int)}
        self.assertEqual(redundant & tracer.hits, set(),
                         "死语句竟被执行: %s" % (redundant & tracer.hits,))
        self.assertEqual(stmts - tracer.hits - redundant, set(),
                         "存在未被任何对拍用例覆盖的语句: %s"
                         % (stmts - tracer.hits - redundant,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
