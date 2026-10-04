"""分层采样库自测（仅标准库 unittest）。

运行：python3 test_sampler.py -v
"""

import unittest

from sampler import Sampler, SamplingRule


def make_mixed_sampler() -> Sampler:
    """ERROR 全保留、INFO 采 50%、DEBUG 全丢弃 的典型配置。"""
    return Sampler(
        rules=[
            SamplingRule(rate=1.0, level="ERROR"),
            SamplingRule(rate=0.5, level="INFO"),
            SamplingRule(rate=0.0, level="DEBUG"),
        ]
    )


class TestKeepAllAndDropAll(unittest.TestCase):
    """情形一/二：全保留与全丢弃。"""

    def test_keep_all(self):
        s = Sampler(rules=[SamplingRule(rate=1.0, level="ERROR")])
        for i in range(1000):
            self.assertTrue(s.should_keep("ERROR", "app", f"req-{i}"))
        row = s.report()[0]
        self.assertEqual(row["actual_rate"], 1.0)
        self.assertEqual(row["deviation"], 0.0)

    def test_drop_all(self):
        s = Sampler(rules=[SamplingRule(rate=0.0, level="DEBUG")])
        for i in range(1000):
            self.assertFalse(s.should_keep("DEBUG", "app", f"req-{i}"))
        row = s.report()[0]
        self.assertEqual(row["actual_rate"], 0.0)
        self.assertEqual(row["deviation"], 0.0)


class TestMixedLevels(unittest.TestCase):
    """情形三：混合级别——错误不采、关键通道不丢、调试全丢。"""

    def test_mixed_levels(self):
        s = make_mixed_sampler()
        for i in range(2000):
            rid = f"req-{i}"
            self.assertTrue(s.should_keep("ERROR", "app", rid))   # 错误日志不能采
            self.assertFalse(s.should_keep("DEBUG", "app", rid))  # 调试全丢
            s.should_keep("INFO", "app", rid)                     # 信息级采 50%
        rows = {r["rule_key"]: r for r in s.report()}
        self.assertEqual(rows["ERROR:*"]["actual_rate"], 1.0)
        self.assertEqual(rows["DEBUG:*"]["actual_rate"], 0.0)
        self.assertLess(rows["INFO:*"]["abs_deviation"], 0.05)

    def test_channel_rule_protects_key_path(self):
        """排查关键路径（payment 通道）全保留，其余 INFO 采 10%。"""
        s = Sampler(
            rules=[
                SamplingRule(rate=1.0, level="INFO", channel="payment"),
                SamplingRule(rate=0.1, level="INFO"),
            ]
        )
        for i in range(2000):
            rid = f"req-{i}"
            self.assertTrue(s.should_keep("INFO", "payment", rid))
            s.should_keep("INFO", "search", rid)
        rows = {r["rule_key"]: r for r in s.report()}
        self.assertEqual(rows["INFO:payment"]["actual_rate"], 1.0)
        self.assertLess(rows["INFO:*"]["abs_deviation"], 0.05)


class TestRequestConsistency(unittest.TestCase):
    """请求级一致性断言：同一请求内同类日志全留或全丢。"""

    def test_same_request_same_fate(self):
        s = Sampler(rules=[SamplingRule(rate=0.3, level="INFO")])
        for i in range(500):
            rid = f"req-{i}"
            # 同一请求产生 20 条同级别日志，决策必须完全一致
            decisions = {s.should_keep("INFO", "app", rid) for _ in range(20)}
            self.assertEqual(len(decisions), 1, f"请求 {rid} 内决策不一致")

    def test_decision_is_deterministic_across_instances(self):
        """同样的输入换一个采样器实例（甚至另一个进程）结果一致。"""
        rules = [SamplingRule(rate=0.3, level="INFO", channel="app")]
        s1, s2 = Sampler(rules=list(rules)), Sampler(rules=list(rules))
        for i in range(500):
            self.assertEqual(
                s1.should_keep("INFO", "app", f"req-{i}"),
                s2.should_keep("INFO", "app", f"req-{i}"),
            )

    def test_request_id_types_normalized(self):
        """字符串与数字请求 ID 归一化后决策一致。"""
        s = Sampler(rules=[SamplingRule(rate=0.3, level="INFO")])
        for i in range(200):
            self.assertEqual(
                s.should_keep("INFO", "app", str(i)),
                s.should_keep("INFO", "app", i),
            )


class TestPriority(unittest.TestCase):
    """匹配优先级：具体度 > priority 数值 > 添加顺序。"""

    def test_specific_beats_wildcard(self):
        s = Sampler(
            rules=[
                SamplingRule(rate=0.0, level="INFO"),                      # 通配通道
                SamplingRule(rate=1.0, level="INFO", channel="payment"),   # 更具体
            ]
        )
        key, rate = s.matched_rule("INFO", "payment")
        self.assertEqual((key, rate), ("INFO:payment", 1.0))
        key, rate = s.matched_rule("INFO", "search")
        self.assertEqual((key, rate), ("INFO:*", 0.0))

    def test_priority_breaks_tie(self):
        s = Sampler(
            rules=[
                SamplingRule(rate=0.0, level="INFO", priority=0),
                SamplingRule(rate=1.0, level="INFO", priority=10),
            ]
        )
        self.assertEqual(s.matched_rule("INFO", "app"), ("INFO:*", 1.0))

    def test_insertion_order_breaks_final_tie(self):
        s = Sampler(
            rules=[
                SamplingRule(rate=0.2, level="INFO"),
                SamplingRule(rate=0.8, level="INFO"),
            ]
        )
        self.assertEqual(s.matched_rule("INFO", "app"), ("INFO:*", 0.2))

    def test_describe_priority_lists_rules_in_order(self):
        s = Sampler(
            rules=[
                SamplingRule(rate=0.0, level="INFO"),
                SamplingRule(rate=1.0, level="INFO", channel="payment"),
            ]
        )
        lines = s.describe_priority()
        self.assertIn("INFO:payment", lines[0])   # 具体度高的排第一
        self.assertIn("default_rate", lines[-1])  # 兜底说明在最后


class TestRuleUpdate(unittest.TestCase):
    """情形四：规则热更新。"""

    def test_update_changes_behavior_and_resets_stats(self):
        s = Sampler(rules=[SamplingRule(rate=0.0, level="INFO")])
        for i in range(100):
            self.assertFalse(s.should_keep("INFO", "app", f"req-{i}"))
        self.assertEqual(s.report()[0]["kept"], 0)

        # 热更新为全保留
        s.update_rules([SamplingRule(rate=1.0, level="INFO")])
        for i in range(100):
            self.assertTrue(s.should_keep("INFO", "app", f"req-{i}"))
        rows = s.report()
        self.assertEqual(len(rows), 1)          # 旧统计已清零，不会新旧混算
        self.assertEqual(rows[0]["total"], 100)
        self.assertEqual(rows[0]["actual_rate"], 1.0)

    def test_update_reorders_priority(self):
        s = Sampler(rules=[SamplingRule(rate=0.5, level="INFO")])
        s.update_rules(
            [
                SamplingRule(rate=0.5, level="INFO"),
                SamplingRule(rate=1.0, level="INFO", channel="payment"),
            ]
        )
        self.assertEqual(s.matched_rule("INFO", "payment"), ("INFO:payment", 1.0))


class TestDeviation(unittest.TestCase):
    """实际采样率与目标采样率的偏差必须可计算且收敛。"""

    def test_deviation_converges(self):
        s = Sampler(rules=[SamplingRule(rate=0.25, level="INFO")])
        n = 20000
        kept = sum(s.should_keep("INFO", "app", f"req-{i}") for i in range(n))
        row = s.report()[0]
        self.assertEqual(row["kept"], kept)
        self.assertAlmostEqual(row["actual_rate"], kept / n)
        self.assertAlmostEqual(row["deviation"], kept / n - 0.25)
        self.assertLess(row["abs_deviation"], 0.02)  # 2 万样本下偏差应 < 2%

    def test_report_contains_all_fields(self):
        s = make_mixed_sampler()
        s.should_keep("INFO", "app", "req-1")
        row = s.report()[0]
        for field in ("rule_key", "target_rate", "total", "kept",
                      "actual_rate", "deviation", "abs_deviation"):
            self.assertIn(field, row)


class TestEdgeCases(unittest.TestCase):
    """边界用例。"""

    def test_invalid_rate_rejected(self):
        for bad in (-0.1, 1.1, 2):
            with self.assertRaises(ValueError):
                SamplingRule(rate=bad)
        with self.assertRaises(TypeError):
            SamplingRule(rate="0.5")

    def test_invalid_default_rate_rejected(self):
        with self.assertRaises(ValueError):
            Sampler(default_rate=1.5)

    def test_no_rule_falls_back_to_default(self):
        s = Sampler(default_rate=1.0)
        key, rate = s.matched_rule("WARN", "unknown-channel")
        self.assertEqual((key, rate), ("__default__", 1.0))
        self.assertTrue(s.should_keep("WARN", "unknown-channel", "req-1"))
        self.assertEqual(s.report()[0]["rule_key"], "__default__")

    def test_empty_rules_and_zero_default(self):
        s = Sampler(default_rate=0.0)
        self.assertFalse(s.should_keep("INFO", "app", "req-1"))

    def test_report_empty_before_any_decision(self):
        s = make_mixed_sampler()
        self.assertEqual(s.report(), [])
        self.assertEqual(s.max_abs_deviation(), 0.0)

    def test_none_level_and_channel_match_wildcard_only(self):
        s = Sampler(rules=[SamplingRule(rate=0.0)])  # 全通配规则
        key, _ = s.matched_rule(None, None)
        self.assertEqual(key, "*:*")
        self.assertFalse(s.should_keep(None, None, "req-1"))


if __name__ == "__main__":
    unittest.main()
