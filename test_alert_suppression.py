"""alert_suppression 的自检测试（仅标准库 unittest）。

运行：python3 -m unittest -v test_alert_suppression
"""

import unittest

from alert_suppression import (
    Alert,
    AlertAggregator,
    AlertPipeline,
    SuppressionRule,
    reconcile,
)


class ManualClock:
    """时间可注入：返回当前设定的 epoch 秒。"""

    def __init__(self, now=0.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def set(self, now):
        self.now = float(now)


def make_alert(alert_id, source, alert_type, timestamp, severity="warning", message=None):
    return Alert(
        alert_id=alert_id,
        source=source,
        alert_type=alert_type,
        severity=severity,
        message=message if message is not None else "%s/%s" % (source, alert_type),
        timestamp=float(timestamp),
    )


class AggregationTest(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock(0.0)

    def test_window_merges_same_source_type_and_keeps_count(self):
        pipeline = AlertPipeline(window_seconds=60.0, clock=self.clock)
        pipeline.ingest(make_alert("a1", "host-1", "disk_full", 0))
        pipeline.ingest(make_alert("a2", "host-1", "disk_full", 10))
        pipeline.ingest(make_alert("a3", "host-1", "disk_full", 59))
        pipeline.flush()

        self.assertEqual(len(pipeline.aggregated), 1)
        agg = pipeline.aggregated[0]
        self.assertEqual(agg.count, 3)
        self.assertEqual(agg.alert_ids, ["a1", "a2", "a3"])
        self.assertEqual((agg.first_seen, agg.last_seen), (0.0, 59.0))

    def test_different_type_or_source_not_merged(self):
        pipeline = AlertPipeline(window_seconds=60.0, clock=self.clock)
        pipeline.ingest(make_alert("a1", "host-1", "disk_full", 0))
        pipeline.ingest(make_alert("a2", "host-1", "cpu_high", 1))
        pipeline.ingest(make_alert("a3", "host-2", "disk_full", 2))
        pipeline.flush()

        keys = {(a.source, a.alert_type) for a in pipeline.aggregated}
        self.assertEqual(keys, {("host-1", "disk_full"), ("host-1", "cpu_high"), ("host-2", "disk_full")})
        self.assertTrue(all(a.count == 1 for a in pipeline.aggregated))

    def test_window_boundary_is_half_open(self):
        # [0, 60) 一个窗口；t=60 恰好落在边界，属于下一个窗口。
        pipeline = AlertPipeline(window_seconds=60.0, clock=self.clock)
        pipeline.ingest(make_alert("a1", "host-1", "cpu_high", 0))
        pipeline.ingest(make_alert("a2", "host-1", "cpu_high", 59.99))
        pipeline.ingest(make_alert("a3", "host-1", "cpu_high", 60.0))
        pipeline.ingest(make_alert("a4", "host-1", "cpu_high", 119.99))
        pipeline.flush()

        counts = sorted(a.count for a in pipeline.aggregated)
        self.assertEqual(counts, [2, 2])
        windows = sorted((a.first_seen, a.last_seen) for a in pipeline.aggregated)
        self.assertEqual(windows, [(0.0, 59.99), (60.0, 119.99)])

    def test_severity_takes_maximum_within_window(self):
        pipeline = AlertPipeline(window_seconds=60.0, clock=self.clock)
        pipeline.ingest(make_alert("a1", "host-1", "disk_full", 0, severity="info"))
        pipeline.ingest(make_alert("a2", "host-1", "disk_full", 5, severity="critical"))
        pipeline.ingest(make_alert("a3", "host-1", "disk_full", 9, severity="warning"))
        pipeline.flush()
        self.assertEqual(pipeline.aggregated[0].max_severity, "critical")

    def test_open_window_visible_before_flush(self):
        pipeline = AlertPipeline(window_seconds=60.0, clock=self.clock)
        pipeline.ingest(make_alert("a1", "host-1", "disk_full", 0))
        open_windows = pipeline.open_windows()
        self.assertEqual(len(open_windows), 1)
        self.assertFalse(open_windows[0].closed)
        self.assertEqual(open_windows[0].count, 1)

    def test_late_arrival_merges_into_current_window(self):
        # 迟到事件（时间戳早于当前窗口锚点）并入当前窗口，不新开窗口。
        pipeline = AlertPipeline(window_seconds=60.0, clock=self.clock)
        pipeline.ingest(make_alert("a2", "host-1", "cpu_high", 10))
        pipeline.ingest(make_alert("a1", "host-1", "cpu_high", 0))  # 迟到
        pipeline.flush()
        self.assertEqual(len(pipeline.aggregated), 1)
        self.assertEqual(pipeline.aggregated[0].count, 2)


class SuppressionTest(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock(0.0)

    def test_single_alert_suppressed_and_traceable(self):
        rule = SuppressionRule("R1", "host-1", "disk_full", reason="维护窗口")
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)
        alert = make_alert("a1", "host-1", "disk_full", 0, severity="critical")
        pipeline.ingest(alert)

        self.assertEqual(len(pipeline.suppressed), 1)
        record = pipeline.suppressed[0]
        self.assertEqual(record.rule_id, "R1")
        self.assertIs(record.alert, alert)
        self.assertEqual(record.reason, "维护窗口")
        self.assertEqual(pipeline.aggregated, [])
        self.assertEqual(pipeline.unaffected, [])

    def test_rule_does_not_match_other_type_or_source(self):
        rule = SuppressionRule("R1", "host-1", "disk_full")
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)
        other_type = make_alert("a1", "host-1", "cpu_high", 0)
        other_source = make_alert("a2", "host-2", "disk_full", 0)
        pipeline.ingest(other_type)
        pipeline.ingest(other_source)
        pipeline.flush()

        self.assertEqual(pipeline.suppressed, [])
        unaffected_ids = [occ.alert.alert_id for occ in pipeline.unaffected]
        self.assertEqual(sorted(unaffected_ids), ["a1", "a2"])
        self.assertEqual(len(pipeline.aggregated), 2)

    def test_same_source_storm_suppressed_with_per_alert_records(self):
        rule = SuppressionRule("R-storm", "host-7", "cpu_high")
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)
        for i in range(1000):
            pipeline.ingest(make_alert("s%04d" % i, "host-7", "cpu_high", i * 0.05))
        pipeline.flush()

        self.assertEqual(len(pipeline.suppressed), 1000)
        self.assertEqual({r.rule_id for r in pipeline.suppressed}, {"R-storm"})
        self.assertEqual(pipeline.aggregated, [])
        self.assertEqual(pipeline.unaffected, [])

    def test_unmatched_alerts_inside_storm_remain_unaffected(self):
        # 风暴被抑制，但同机其他类型 / 他机同类型告警必须原样保留。
        rule = SuppressionRule("R-storm", "host-7", "cpu_high")
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)
        for i in range(20):
            pipeline.ingest(make_alert("storm%d" % i, "host-7", "cpu_high", i))
        keep_same_host = make_alert("k1", "host-7", "disk_full", 3, severity="critical")
        keep_other_host = make_alert("k2", "host-8", "cpu_high", 4, severity="warning")
        pipeline.ingest(keep_same_host)
        pipeline.ingest(keep_other_host)
        pipeline.flush()

        unaffected_ids = [occ.alert.alert_id for occ in pipeline.unaffected]
        self.assertEqual(sorted(unaffected_ids), ["k1", "k2"])
        self.assertEqual(len(pipeline.suppressed), 20)
        # 未受影响的是完整 Alert 对象，字段都可核对。
        k1 = next(occ.alert for occ in pipeline.unaffected if occ.alert.alert_id == "k1")
        self.assertEqual((k1.source, k1.alert_type, k1.severity), ("host-7", "disk_full", "critical"))

    def test_rule_covers_critical_alert_within_active_window(self):
        # 规则存在时间窗 [100, 200)；窗内的关键告警被覆盖，窗外恢复上报。
        rule = SuppressionRule("R-deploy", "api-1", "cpu_high", starts_at=100.0, ends_at=200.0)
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)

        self.clock.set(150.0)
        pipeline.ingest(make_alert("in1", "api-1", "cpu_high", 150, severity="critical"))
        self.clock.set(100.0)
        pipeline.ingest(make_alert("in2", "api-1", "cpu_high", 100, severity="critical"))  # 含起点
        self.clock.set(200.0)
        pipeline.ingest(make_alert("out1", "api-1", "cpu_high", 200, severity="critical"))  # 不含终点
        self.clock.set(99.9)
        pipeline.ingest(make_alert("out2", "api-1", "cpu_high", 99.9, severity="critical"))
        pipeline.flush()

        suppressed_ids = sorted(r.alert.alert_id for r in pipeline.suppressed)
        self.assertEqual(suppressed_ids, ["in1", "in2"])
        unaffected_ids = sorted(occ.alert.alert_id for occ in pipeline.unaffected)
        self.assertEqual(unaffected_ids, ["out1", "out2"])

    def test_expired_rule_does_not_suppress(self):
        rule = SuppressionRule("R-old", "host-1", "disk_full", starts_at=0.0, ends_at=50.0)
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)
        self.clock.set(49.99)
        pipeline.ingest(make_alert("before", "host-1", "disk_full", 49.99))
        self.clock.set(50.0)
        pipeline.ingest(make_alert("after", "host-1", "disk_full", 50.0))
        pipeline.flush()

        self.assertEqual([r.alert.alert_id for r in pipeline.suppressed], ["before"])
        unaffected_ids = [occ.alert.alert_id for occ in pipeline.unaffected]
        self.assertEqual(unaffected_ids, ["after"])

    def test_future_rule_not_yet_active(self):
        rule = SuppressionRule("R-future", "host-1", "disk_full", starts_at=100.0)
        self.clock.set(10.0)
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)
        pipeline.ingest(make_alert("early", "host-1", "disk_full", 10))
        self.assertEqual(pipeline.suppressed, [])
        self.assertEqual([occ.alert.alert_id for occ in pipeline.unaffected], ["early"])

    def test_open_ended_rule_stays_active(self):
        rule = SuppressionRule("R-forever", "host-1", "disk_full")
        pipeline = AlertPipeline(rules=[rule], window_seconds=60.0, clock=self.clock)
        for now in (0.0, 10**9):
            self.clock.set(now)
            self.assertIsNotNone(pipeline._engine.evaluate(make_alert("x", "host-1", "disk_full", now)))


class ReconciliationTest(unittest.TestCase):
    def test_every_input_alert_accounted_once(self):
        self.clock = ManualClock(300.0)
        rules = [
            SuppressionRule("R1", "host-1", "disk_full"),
            SuppressionRule("R2", "api-1", "cpu_high", starts_at=0.0, ends_at=200.0),
        ]
        pipeline = AlertPipeline(rules=rules, window_seconds=60.0, clock=self.clock)
        alerts = [
            make_alert("a1", "host-1", "disk_full", 10, severity="critical"),
            make_alert("a2", "host-1", "disk_full", 20),
            make_alert("a3", "host-2", "disk_full", 30),
            make_alert("a4", "host-7", "cpu_high", 0),
            make_alert("a5", "host-7", "cpu_high", 30),
            make_alert("a6", "host-7", "cpu_high", 70),
        ]
        for alert in alerts:
            pipeline.ingest(alert)
        pipeline.flush()

        result = reconcile([a.alert_id for a in alerts], pipeline)
        self.assertTrue(result.ok, result)
        self.assertEqual(result.total_input, 6)
        self.assertEqual(result.suppressed, 2)
        self.assertEqual(result.aggregated_members, 4)

    def test_duplicate_alert_id_rejected(self):
        pipeline = AlertPipeline(window_seconds=60.0, clock=ManualClock(0.0))
        pipeline.ingest(make_alert("dup", "h", "t", 0))
        with self.assertRaises(ValueError):
            pipeline.ingest(make_alert("dup", "h", "t", 1))


class AggregatorStandaloneTest(unittest.TestCase):
    def test_non_positive_window_rejected(self):
        with self.assertRaises(ValueError):
            AlertAggregator(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
