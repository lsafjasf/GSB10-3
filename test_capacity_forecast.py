"""capacity_forecast 自测：平稳 / 单调增长 / 强周期 / 数据不足 / 边界用例。"""

import math
import random
import unittest

from capacity_forecast import SECONDS_PER_DAY, alert_level, forecast

DAY = SECONDS_PER_DAY
T0 = 1_700_000_000.0  # 固定起点，保证测试可复现


def make_ts(n, step_days=1.0, start=T0):
    return [start + i * step_days * DAY for i in range(n)]


class TestStationary(unittest.TestCase):
    """平稳数据：无增长趋势，视界内不应报达到阈值。"""

    def test_stationary_never_reaches(self):
        rng = random.Random(1)
        ts = make_ts(60)
        vals = [40.0 + rng.uniform(-0.5, 0.5) for _ in ts]
        rep = forecast(ts, vals, threshold=90.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertIsNone(rep.crossing_time)
        self.assertAlmostEqual(rep.slope_per_day, 0.0, delta=0.05)
        self.assertGreater(rep.metrics.r2, 0.0)  # 常数+噪声，拟合应可用
        alert = alert_level(rep)
        self.assertEqual(alert["level"], "safe")

    def test_constant_series(self):
        ts = make_ts(30)
        vals = [50.0] * 30
        rep = forecast(ts, vals, threshold=80.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertIsNone(rep.crossing_time)
        self.assertAlmostEqual(rep.metrics.rmse, 0.0, places=6)


class TestMonotonicGrowth(unittest.TestCase):
    """单调增长：交叉时间应接近真值，且置信区间覆盖真值。"""

    def test_linear_growth_crossing(self):
        # y = 10 + 0.5t（t 单位：天），阈值 80 → 真值第 140 天
        ts = make_ts(60)
        vals = [10.0 + 0.5 * i for i in range(60)]
        rep = forecast(ts, vals, threshold=80.0)
        self.assertTrue(rep.ok, rep.reason)
        true_cross = T0 + 140 * DAY
        self.assertIsNotNone(rep.crossing_time)
        self.assertAlmostEqual(rep.crossing_time, true_cross, delta=DAY)
        # 置信区间应覆盖真值
        self.assertLessEqual(rep.crossing_earliest, true_cross + DAY)
        self.assertGreaterEqual(rep.crossing_latest, true_cross - DAY)
        self.assertAlmostEqual(rep.slope_per_day, 0.5, places=6)
        self.assertGreater(rep.metrics.r2, 0.999)

    def test_growth_with_noise_interval_covers_truth(self):
        rng = random.Random(7)
        ts = make_ts(90)
        vals = [20.0 + 0.3 * i + rng.gauss(0, 1.0) for i in range(90)]
        rep = forecast(ts, vals, threshold=80.0)
        self.assertTrue(rep.ok, rep.reason)
        true_cross = T0 + 200 * DAY  # (80-20)/0.3
        self.assertLess(rep.crossing_earliest, true_cross)
        self.assertGreater(rep.crossing_latest, true_cross)
        alert = alert_level(rep, warning_days=300)
        self.assertIn(alert["level"], ("warning", "watch", "critical"))


class TestStrongPeriodic(unittest.TestCase):
    """强周期：识别周期、拟合良好，且避免波峰误报。"""

    def _weekly_data(self, n=84, base=40.0, slope=0.1, amp=15.0, noise=0.3):
        rng = random.Random(3)
        ts = make_ts(n, step_days=1.0)
        vals = [base + slope * i + amp * math.sin(2 * math.pi * i / 7.0)
                + rng.gauss(0, noise) for i in range(n)]
        return ts, vals

    def test_period_detected_and_fit_good(self):
        ts, vals = self._weekly_data()
        rep = forecast(ts, vals, threshold=200.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertIsNotNone(rep.period_days)
        self.assertAlmostEqual(rep.period_days, 7.0, delta=0.5)
        self.assertGreater(rep.metrics.r2, 0.95)
        # 拟合误差数据完整
        self.assertEqual(len(rep.metrics.fitted), len(vals))
        self.assertEqual(len(rep.metrics.residuals), len(vals))
        self.assertLess(rep.metrics.rmse, 1.0)

    def test_no_false_alarm_at_peak(self):
        # 60 天历史，波峰包络 55+0.05t，历史峰值最高约 58 < 阈值 60；
        # 简单线性外推在波峰附近会很快误报，而真实首次触阈约在
        # 第 (60-55)/0.05 = 100 天，即约 40 天之后。
        ts, vals = self._weekly_data(n=60, base=40.0, slope=0.05, amp=15.0)
        rep = forecast(ts, vals, threshold=60.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertLess(max(vals), 60.0)  # 前提：历史从未触阈
        self.assertIsNotNone(rep.crossing_time)
        days_ahead = (rep.crossing_time - ts[-1]) / DAY
        self.assertGreater(days_ahead, 20.0,
                           "周期模型不应在波峰附近过早误报")
        # 也不应晚得离谱（真值约 40 天后）
        self.assertLess(days_ahead, 80.0)

    def test_periodic_crossing_close_to_truth(self):
        # 无噪声、已知解析式：峰值触阈时间可解析估计
        ts, vals = self._weekly_data(n=84, base=40.0, slope=0.2, amp=10.0, noise=0.0)
        rep = forecast(ts, vals, threshold=70.0)
        self.assertTrue(rep.ok, rep.reason)
        # 峰值包络 40+0.2t+10 >= 70 → t >= 100 天；考虑正弦相位，
        # 首次触阈在 t≈106 天附近的波峰上升沿，即约 22 天后
        days_ahead = (rep.crossing_time - ts[-1]) / DAY
        self.assertGreater(days_ahead, 12.0)
        self.assertLess(days_ahead, 30.0)


class TestInsufficientData(unittest.TestCase):
    """数据点过少：必须拒绝并说明依据。"""

    def test_too_few_points_rejected(self):
        ts = make_ts(5)
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        rep = forecast(ts, vals, threshold=100.0)
        self.assertFalse(rep.ok)
        self.assertIn("数据点不足", rep.reason)
        self.assertEqual(alert_level(rep)["level"], "unknown")

    def test_empty_and_single_point(self):
        self.assertFalse(forecast([], [], 1.0).ok)
        self.assertFalse(forecast([T0], [1.0], 100.0).ok)

    def test_duplicate_timestamps_shrink_below_min(self):
        ts = [T0] * 10  # 去重后只剩 1 个点
        vals = list(range(10))
        rep = forecast(ts, vals, 100.0)
        self.assertFalse(rep.ok)
        self.assertIn("数据点不足", rep.reason)


class TestVolatileData(unittest.TestCase):
    """波动过大：必须拒绝并说明依据。"""

    def test_pure_noise_rejected(self):
        rng = random.Random(42)
        ts = make_ts(80)
        vals = [rng.uniform(0, 100) for _ in ts]
        rep = forecast(ts, vals, threshold=120.0)
        self.assertFalse(rep.ok)
        self.assertIn("波动过大", rep.reason)
        self.assertIn("R^2", rep.reason)


class TestEdgeCases(unittest.TestCase):
    def test_unsorted_input_accepted(self):
        ts = make_ts(40)
        vals = [5.0 + 0.4 * i for i in range(40)]
        pairs = list(zip(ts, vals))
        random.Random(9).shuffle(pairs)
        ts_shuf = [p[0] for p in pairs]
        vals_shuf = [p[1] for p in pairs]
        rep = forecast(ts_shuf, vals_shuf, threshold=50.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertAlmostEqual(rep.slope_per_day, 0.4, places=6)

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            forecast([T0, T0 + DAY], [1.0], 10.0)

    def test_threshold_already_exceeded(self):
        ts = make_ts(30)
        vals = [95.0 + 0.1 * i for i in range(30)]
        rep = forecast(ts, vals, threshold=90.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertEqual(rep.crossing_time, ts[-1])
        self.assertEqual(alert_level(rep)["level"], "exceeded")

    def test_declining_usage_safe(self):
        ts = make_ts(40)
        vals = [70.0 - 0.5 * i for i in range(40)]
        rep = forecast(ts, vals, threshold=90.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertIsNone(rep.crossing_time)
        self.assertEqual(alert_level(rep)["level"], "safe")

    def test_alert_levels(self):
        # 2 天后触阈 → critical
        ts = make_ts(30)
        vals = [10.0 + 1.0 * i for i in range(30)]  # 最后一天 39
        rep = forecast(ts, vals, threshold=41.0)
        self.assertTrue(rep.ok, rep.reason)
        self.assertEqual(alert_level(rep)["level"], "critical")
        # 10 天后触阈 → warning
        rep2 = forecast(ts, vals, threshold=49.0)
        self.assertEqual(alert_level(rep2)["level"], "warning")


if __name__ == "__main__":
    unittest.main(verbosity=2)
