"""分层采样库自测（标准库 unittest）。

运行：python3 -m unittest discover -s tests -v   （仓库根目录下）
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sampling import (
    BufferedRequestSampler,
    Event,
    HeadRequestSampler,
    InconsistentRequestError,
    LayeredSampler,
    Level,
    Metrics,
    Rule,
    assert_request_consistency,
)


def ev(rid, level, channel="http", path="/api/normal"):
    return Event(rid, level, channel, "msg", path)


class TestRuleValidation(unittest.TestCase):
    """边界：非法规则必须被拒绝。"""

    def test_rate_out_of_range(self):
        with self.assertRaises(ValueError):
            Rule(Level.DEBUG, rate=1.5)
        with self.assertRaises(ValueError):
            Rule(Level.DEBUG, rate=-0.1)

    def test_missing_rate(self):
        with self.assertRaises(ValueError):
            Rule(Level.DEBUG).effective_rate

    def test_conflicting_keep_drop(self):
        with self.assertRaises(ValueError):
            Rule(Level.DEBUG, keep=True, drop=True)
        with self.assertRaises(ValueError):
            Rule(Level.DEBUG, rate=0.5, keep=True)

    def test_default_rate_out_of_range(self):
        with self.assertRaises(ValueError):
            LayeredSampler(default_rate=2.0)

    def test_level_normalization(self):
        self.assertIs(Level.normalize("error"), Level.ERROR)
        self.assertIs(Level.normalize(30), Level.WARN)
        with self.assertRaises(KeyError):
            Level.normalize("nope")


class TestMatchPriority(unittest.TestCase):
    """匹配优先级：强制保留 > 级别+通道 > 级别 > 通道 > 默认。"""

    def setUp(self):
        self.sampler = LayeredSampler(
            rules=[
                Rule(Level.INFO, "billing", rate=0.9, name="lc"),
                Rule(Level.INFO, rate=0.3, name="lvl"),
                Rule(None, "db", rate=0.5, name="chan"),
                Rule(None, "auth", keep=True, name="keep"),
            ],
            default_rate=0.1,
            protected_channels={"payments"},
            protected_path_prefixes=("/api/critical",),
        )

    def test_level_channel_beats_level(self):
        m = self.sampler.match(ev("r", Level.INFO, "billing"))
        self.assertEqual(m.rate, 0.9)
        self.assertEqual(m.priority, 1)

    def test_level_beats_default(self):
        m = self.sampler.match(ev("r", Level.INFO, "http"))
        self.assertEqual(m.rate, 0.3)
        self.assertEqual(m.priority, 2)

    def test_channel_beats_default(self):
        m = self.sampler.match(ev("r", Level.DEBUG, "db"))
        self.assertEqual(m.rate, 0.5)
        self.assertEqual(m.priority, 3)

    def test_default_fallback(self):
        m = self.sampler.match(ev("r", Level.DEBUG, "http"))
        self.assertEqual(m.rate, 0.1)
        self.assertEqual(m.priority, 4)

    def test_error_never_sampled(self):
        # 即使 default_rate=0 且有 drop 规则，ERROR/FATAL 也强制保留
        s = LayeredSampler(
            rules=[Rule(Level.ERROR, rate=0.0, name="try-drop")],
            default_rate=0.0,
        )
        for lvl in (Level.ERROR, Level.FATAL):
            m = s.match(ev("r", lvl))
            self.assertEqual(m.rate, 1.0)
            self.assertEqual(m.priority, 0)

    def test_protected_channel_and_path(self):
        m = self.sampler.match(ev("r", Level.DEBUG, "payments"))
        self.assertEqual(m.rate, 1.0)
        m = self.sampler.match(
            ev("r", Level.DEBUG, "http", path="/api/critical/x")
        )
        self.assertEqual(m.rate, 1.0)

    def test_explicit_keep_rule_beats_sampling(self):
        m = self.sampler.match(ev("r", Level.DEBUG, "auth"))
        self.assertEqual(m.rate, 1.0)
        self.assertEqual(m.priority, 0)

    def test_earlier_rule_wins_within_same_tier(self):
        s = LayeredSampler(
            rules=[
                Rule(Level.WARN, rate=0.7, name="first"),
                Rule(Level.WARN, rate=0.2, name="second"),
            ]
        )
        self.assertEqual(s.match(ev("r", Level.WARN)).rate, 0.7)


class TestRuleUpdate(unittest.TestCase):
    """规则可原子更新；进行中的请求按旧快照保持一致。"""

    def test_atomic_update_and_version(self):
        s = LayeredSampler(rules=[Rule(Level.DEBUG, rate=0.1)], default_rate=0.5)
        v0 = s.version
        v1 = s.update_rules([Rule(Level.DEBUG, rate=0.9)])
        self.assertEqual(v1, v0 + 1)
        self.assertEqual(s.match(ev("r", Level.DEBUG)).rate, 0.9)
        # 未指定的项沿用旧配置
        self.assertEqual(s.match(ev("r", Level.WARN)).rate, 0.5)

    def test_invalid_update_rejected_atomically(self):
        s = LayeredSampler(rules=[Rule(Level.DEBUG, rate=0.1)])
        with self.assertRaises(ValueError):
            s.update_rules([Rule(Level.DEBUG, rate=9.9)])
        self.assertEqual(s.match(ev("r", Level.DEBUG)).rate, 0.1)

    def test_inflight_request_pinned_to_old_snapshot(self):
        # 规则从 0.0 更新到 1.0，进行中的请求仍按旧快照全部丢弃
        s = LayeredSampler(default_rate=0.0)
        rs = BufferedRequestSampler(s)
        rs.record(ev("req-1", Level.INFO))
        rs.record(ev("req-1", Level.DEBUG))
        s.update_rules([], default_rate=1.0)
        result = rs.end_request("req-1")
        self.assertFalse(result.kept)
        self.assertEqual(result.events, [])
        self.assertEqual(result.rule_version, 0)
        # 新请求用新规则
        rs.record(ev("req-2", Level.INFO))
        self.assertTrue(rs.end_request("req-2").kept)


class TestRequestConsistency(unittest.TestCase):
    """请求级一致性：同一请求全留或全丢。"""

    def test_assert_helper(self):
        assert_request_consistency("r", [True, True])
        assert_request_consistency("r", [False, False])
        assert_request_consistency("r", [])
        with self.assertRaises(InconsistentRequestError):
            assert_request_consistency("r", [True, False])

    def test_buffered_all_or_nothing_over_many_requests(self):
        s = LayeredSampler(
            rules=[Rule(Level.DEBUG, rate=0.3), Rule(Level.INFO, rate=0.6)],
            default_rate=0.5,
        )
        rng = random.Random(7)
        rs = BufferedRequestSampler(s, rng=random.Random(7).random)
        for i in range(500):
            rid = f"r{i}"
            levels = [rng.choice(list(Level)) for _ in range(rng.randint(1, 8))]
            for lvl in levels:
                rs.record(ev(rid, lvl))
            result = rs.end_request(rid)
            kept_ids = {e.request_id for e in result.events}
            flags = [rid in kept_ids] * len(levels)
            assert_request_consistency(rid, flags)  # 不抛即一致
            self.assertIn(len(result.events), (0, len(levels)))

    def test_error_anywhere_forces_keep(self):
        s = LayeredSampler(default_rate=0.0)  # 默认全丢
        rs = BufferedRequestSampler(s)
        rs.record(ev("r", Level.DEBUG))
        rs.record(ev("r", Level.INFO))
        rs.record(ev("r", Level.ERROR))  # 错误出现在请求末尾
        result = rs.end_request("r")
        self.assertTrue(result.kept)
        self.assertEqual(len(result.events), 3)

    def test_protected_path_forces_keep(self):
        s = LayeredSampler(
            default_rate=0.0, protected_path_prefixes=("/api/critical",)
        )
        rs = BufferedRequestSampler(s)
        rs.record(ev("r", Level.DEBUG, path="/api/critical/pay"))
        rs.record(ev("r", Level.DEBUG))
        self.assertTrue(rs.end_request("r").kept)

    def test_unknown_request_raises(self):
        rs = BufferedRequestSampler(LayeredSampler())
        with self.assertRaises(KeyError):
            rs.end_request("ghost")

    def test_head_mode_upgrade_on_late_error(self):
        s = LayeredSampler(default_rate=0.0)
        hs = HeadRequestSampler(s)
        self.assertFalse(hs.should_emit(ev("r", Level.DEBUG)))
        self.assertFalse(hs.should_emit(ev("r", Level.INFO)))
        # 迟到的 ERROR 升级整个请求；之前丢掉的通过影子缓冲补放
        self.assertTrue(hs.should_emit(ev("r", Level.ERROR)))
        shadow = hs.end_request("r")
        self.assertEqual([e.level for e in shadow], [Level.DEBUG, Level.INFO])

    def test_head_mode_decision_cached(self):
        s = LayeredSampler(rules=[Rule(Level.INFO, rate=0.5)])
        hs = HeadRequestSampler(s, rng=lambda: 0.9)  # 0.9 >= 0.5 -> 丢
        self.assertFalse(hs.should_emit(ev("r", Level.INFO)))
        # 后续同级别事件复用缓存决策，不会重新掷骰
        self.assertFalse(hs.should_emit(ev("r", Level.INFO)))


class TestRatesQuantitative(unittest.TestCase):
    """定量：实际采样率与目标采样率的偏差在统计容差内。"""

    def _run(self, rules, default_rate, n=4000, seed=1):
        s = LayeredSampler(rules=rules, default_rate=default_rate)
        rs = BufferedRequestSampler(s, rng=random.Random(seed).random)
        metrics = Metrics()
        gen = random.Random(seed + 100)
        for i in range(n):
            rid = f"r{i}"
            # 每请求单一 (级别, 通道)，使分组目标率可与实际率直接对照
            lvl = gen.choice([Level.DEBUG, Level.INFO, Level.WARN])
            chan = gen.choice(["http", "db"])
            for _ in range(gen.randint(1, 5)):
                rs.record(ev(rid, lvl, chan))
            result = rs.end_request(rid)
            kept = bool(result.events)
            metrics.record(ev(rid, lvl, chan), kept, result.match.rate)
        return metrics

    def test_keep_all(self):
        m = self._run([], 1.0)
        for g in m.groups():
            self.assertEqual(g.actual_rate, 1.0)
            self.assertEqual(g.abs_error, 0.0)

    def test_drop_all(self):
        m = self._run([], 0.0)
        for g in m.groups():
            self.assertEqual(g.actual_rate, 0.0)
            self.assertEqual(g.abs_error, 0.0)

    def test_mixed_rates_within_ci(self):
        m = self._run(
            [
                Rule(Level.DEBUG, rate=0.1),
                Rule(Level.INFO, rate=0.5),
                Rule(Level.WARN, rate=0.9),
                Rule(Level.INFO, "db", rate=0.25),
            ],
            default_rate=1.0,
        )
        checked = 0
        for g in m.groups():
            if g.target_rate in (0.0, 1.0):
                self.assertEqual(g.actual_rate, g.target_rate)
                continue
            self.assertLessEqual(
                g.abs_error,
                g.ci95_halfwidth,
                f"{g.level.name}/{g.channel}: 实际 {g.actual_rate:.4f} "
                f"vs 目标 {g.target_rate}",
            )
            checked += 1
        self.assertGreaterEqual(checked, 5)


class TestEdgeCases(unittest.TestCase):
    def test_empty_request_list_flush_all(self):
        rs = BufferedRequestSampler(LayeredSampler())
        self.assertEqual(rs.flush_all(), [])

    def test_single_event_request(self):
        rs = BufferedRequestSampler(LayeredSampler(default_rate=1.0))
        rs.record(ev("solo", Level.INFO))
        result = rs.end_request("solo")
        self.assertTrue(result.kept)
        self.assertEqual(len(result.events), 1)

    def test_buffer_limit(self):
        rs = BufferedRequestSampler(LayeredSampler(), max_buffered_events=2)
        rs.record(ev("a", Level.INFO))
        rs.record(ev("b", Level.INFO))
        with self.assertRaises(RuntimeError):
            rs.record(ev("c", Level.INFO))

    def test_event_level_coercion(self):
        e = Event("r", "warn", "http")
        self.assertIs(e.level, Level.WARN)

    def test_metrics_rel_error_zero_target(self):
        m = Metrics()
        e = ev("r", Level.DEBUG)
        m.record(e, False, 0.0)
        g = m.groups()[0]
        self.assertEqual(g.rel_error, 0.0)


if __name__ == "__main__":
    unittest.main()
