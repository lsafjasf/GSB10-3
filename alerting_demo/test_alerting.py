"""alerting 库自测（标准库 unittest，时间全部注入，无真实时钟依赖）。

覆盖情形：
  1. 覆盖单条告警
  2. 同源风暴聚合
  3. 抑制规则不得覆盖关键告警
  4. 规则过期
  5. 抑制不影响未匹配规则的告警（完整清单）
  6. 抑制记录可追溯
  7. 边界：空规则禁止、窗口边界、不同来源不合并、时间注入
"""

import unittest

from alerting import (
    Alert,
    AlertEngine,
    Severity,
    SuppressionRule,
)


def make_alert(alert_id, source="host-1", alert_type="cpu_high",
               severity=Severity.WARNING, message="cpu > 90%",
               timestamp=1000.0, labels=None):
    return Alert.create(
        alert_id=alert_id,
        source=source,
        alert_type=alert_type,
        severity=severity,
        message=message,
        timestamp=timestamp,
        labels=labels,
    )


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestSingleAlertSuppression(unittest.TestCase):
    """情形 1：规则精确覆盖单条告警。"""

    def test_single_alert_suppressed(self):
        clock = FakeClock()
        rule = SuppressionRule.create(
            rule_id="R1",
            reason="host-1 计划内维护",
            source="host-1",
            alert_type="cpu_high",
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        result = engine.ingest(make_alert("a1"))

        self.assertTrue(result.suppressed)
        self.assertEqual(engine.suppressed_alert_ids(), ["a1"])
        self.assertEqual(engine.unaffected_alert_ids(), [])
        self.assertEqual(engine.close_window(), [])

    def test_rule_requires_at_least_one_criterion(self):
        with self.assertRaises(ValueError):
            SuppressionRule.create(rule_id="R0", reason="空规则")


class TestSameSourceStorm(unittest.TestCase):
    """情形 2：同源风暴在窗口内合并成一条并保留计数。"""

    def test_storm_merged_with_count(self):
        clock = FakeClock()
        engine = AlertEngine(rules=[], window_seconds=60, clock=clock)

        for i in range(100):
            engine.ingest(
                make_alert(f"storm-{i}", timestamp=1000.0 + i * 0.5)
            )

        groups = engine.close_window()
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.source, "host-1")
        self.assertEqual(group.alert_type, "cpu_high")
        self.assertEqual(group.count, 100)
        self.assertEqual(group.first_seen, 1000.0)
        self.assertEqual(group.last_seen, 1049.5)
        self.assertEqual(len(group.member_ids), 100)

    def test_window_rollover_creates_new_group(self):
        clock = FakeClock()
        engine = AlertEngine(rules=[], window_seconds=60, clock=clock)

        engine.ingest(make_alert("a1", timestamp=1000.0))
        result = engine.ingest(make_alert("a2", timestamp=1061.0))  # 超出窗口

        self.assertEqual(len(result.flushed), 1)
        self.assertEqual(result.flushed[0].count, 1)
        groups = engine.close_window()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].member_ids, ["a2"])

    def test_window_boundary_inclusive(self):
        """距 first_seen 恰好等于 window 的告警仍属于当前窗口。"""
        clock = FakeClock()
        engine = AlertEngine(rules=[], window_seconds=60, clock=clock)

        engine.ingest(make_alert("a1", timestamp=1000.0))
        result = engine.ingest(make_alert("a2", timestamp=1060.0))

        self.assertEqual(result.flushed, [])
        self.assertEqual(engine.close_window()[0].count, 2)

    def test_different_sources_not_merged(self):
        clock = FakeClock()
        engine = AlertEngine(rules=[], window_seconds=60, clock=clock)

        engine.ingest(make_alert("a1", source="host-1"))
        engine.ingest(make_alert("a2", source="host-2"))
        engine.ingest(make_alert("a3", source="host-1", alert_type="disk_full"))

        groups = engine.close_window()
        self.assertEqual(len(groups), 3)


class TestCriticalAlertNotSuppressed(unittest.TestCase):
    """情形 3：抑制规则只压低级别告警，关键告警必须放行。"""

    def test_critical_alert_passes_through(self):
        clock = FakeClock()
        rule = SuppressionRule.create(
            rule_id="R-low",
            reason="压掉 host-1 的 WARNING 及以下噪音",
            source="host-1",
            min_severity=Severity.WARNING,
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        noise = engine.ingest(make_alert("noise", severity=Severity.WARNING))
        critical = engine.ingest(
            make_alert("crit", severity=Severity.CRITICAL,
                       message="主库不可用")
        )

        self.assertTrue(noise.suppressed)
        self.assertFalse(critical.suppressed)
        self.assertEqual(engine.suppressed_alert_ids(), ["noise"])
        self.assertEqual(engine.unaffected_alert_ids(), ["crit"])
        groups = engine.close_window()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].max_severity, Severity.CRITICAL)

    def test_label_scoped_rule_does_not_leak(self):
        """带标签限定的规则不会误伤同来源但标签不同的告警。"""
        clock = FakeClock()
        rule = SuppressionRule.create(
            rule_id="R-canary",
            reason="灰度环境噪音",
            source="host-1",
            labels={"env": "canary"},
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        canary = engine.ingest(
            make_alert("c1", labels={"env": "canary"})
        )
        prod = engine.ingest(
            make_alert("p1", labels={"env": "prod"})
        )

        self.assertTrue(canary.suppressed)
        self.assertFalse(prod.suppressed)


class TestRuleExpiry(unittest.TestCase):
    """情形 4：规则过期后不再压制。"""

    def test_expired_rule_stops_suppressing(self):
        clock = FakeClock(1000.0)
        rule = SuppressionRule.create(
            rule_id="R-maint",
            reason="维护窗口 1000~1100",
            source="host-1",
            starts_at=1000.0,
            expires_at=1100.0,
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        during = engine.ingest(make_alert("during", timestamp=1050.0))
        clock.advance(150.0)  # now = 1150
        after = engine.ingest(make_alert("after", timestamp=1150.0))

        self.assertTrue(during.suppressed)
        self.assertFalse(after.suppressed)
        self.assertEqual(engine.suppressed_alert_ids(), ["during"])
        self.assertEqual(engine.unaffected_alert_ids(), ["after"])

    def test_rule_not_yet_active(self):
        clock = FakeClock(500.0)
        rule = SuppressionRule.create(
            rule_id="R-future",
            reason="未来才生效",
            source="host-1",
            starts_at=1000.0,
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        result = engine.ingest(make_alert("early", timestamp=500.0))
        self.assertFalse(result.suppressed)


class TestUnaffectedIsolation(unittest.TestCase):
    """情形 5：抑制不得影响未匹配规则的告警，且清单完整。"""

    def test_unmatched_alerts_untouched_and_listed(self):
        clock = FakeClock()
        rule = SuppressionRule.create(
            rule_id="R1",
            reason="只压 host-1 的 cpu_high",
            source="host-1",
            alert_type="cpu_high",
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        engine.ingest(make_alert("s1", source="host-1", alert_type="cpu_high"))
        engine.ingest(make_alert("u1", source="host-1", alert_type="disk_full"))
        engine.ingest(make_alert("u2", source="host-2", alert_type="cpu_high"))
        engine.ingest(make_alert("u3", source="db-1", alert_type="replication_lag",
                                 severity=Severity.ERROR))

        self.assertEqual(engine.suppressed_alert_ids(), ["s1"])
        self.assertEqual(engine.unaffected_alert_ids(), ["u1", "u2", "u3"])
        groups = engine.close_window()
        self.assertEqual(len(groups), 3)
        keys = {(g.source, g.alert_type) for g in groups}
        self.assertEqual(
            keys,
            {("host-1", "disk_full"), ("host-2", "cpu_high"),
             ("db-1", "replication_lag")},
        )


class TestSuppressionTraceability(unittest.TestCase):
    """情形 6：每条被压制的告警都可追溯到具体规则与原因。"""

    def test_suppression_record_is_traceable(self):
        clock = FakeClock(2000.0)
        rule = SuppressionRule.create(
            rule_id="R-trace",
            reason="升级期间静默",
            source="web-*",
            alert_type="http_5xx",
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        engine.ingest(make_alert("t1", source="web-3", alert_type="http_5xx"))

        self.assertEqual(len(engine.suppression_records), 1)
        record = engine.suppression_records[0]
        self.assertEqual(record.alert.alert_id, "t1")
        self.assertEqual(record.rule.rule_id, "R-trace")
        self.assertEqual(record.rule.reason, "升级期间静默")
        self.assertEqual(record.suppressed_at, 2000.0)
        as_dict = record.to_dict()
        self.assertEqual(as_dict["rule_id"], "R-trace")
        self.assertEqual(as_dict["alert_id"], "t1")


class TestTimeInjection(unittest.TestCase):
    """边界：时钟完全由外部注入，可重复、可测试。"""

    def test_injected_clock_controls_suppression_window(self):
        clock = FakeClock(0.0)
        rule = SuppressionRule.create(
            rule_id="R1", reason="仅前 10 秒", source="host-1",
            expires_at=10.0,
        )
        engine = AlertEngine(rules=[rule], window_seconds=60, clock=clock)

        self.assertTrue(engine.ingest(make_alert("a")).suppressed)
        clock.advance(20.0)
        self.assertFalse(engine.ingest(make_alert("b")).suppressed)

    def test_explicit_timestamp_overrides_clock(self):
        clock = FakeClock(9999.0)
        engine = AlertEngine(rules=[], window_seconds=60, clock=clock)
        result = engine.ingest(make_alert("a"), timestamp=1234.0)
        self.assertFalse(result.suppressed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
