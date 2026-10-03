"""自适应批大小控制器自测（unittest，仅标准库，含注入时钟）。

运行：python3 -m unittest test_adaptive_batch -v
或：  python3 test_adaptive_batch.py
"""
import math
import random
import unittest

from adaptive_batch import AdaptiveBatchSizer


class FakeClock:
    """可手动推进的注入时钟。"""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestBasicAdjustment(unittest.TestCase):
    def setUp(self):
        self.sizer = AdaptiveBatchSizer(
            min_size=10, max_size=500, initial_size=100,
            target_latency=0.5, deadband=0.10,
            failure_threshold=0.20, cooldown_rounds=2)

    def test_high_latency_shrinks_multiplicatively(self):
        self.assertEqual(self.sizer.record(1.0, 0), 50)        # 100 * 0.5
        self.assertEqual(self.sizer.history[-1].reason, "latency_high")

    def test_failure_shrinks_multiplicatively(self):
        self.assertEqual(self.sizer.record(0.1, 50), 50)       # 50% 失败
        self.assertEqual(self.sizer.history[-1].reason, "failure")

    def test_low_latency_grows_additively(self):
        # 先记录几轮正常耗时，避免冷却
        self.sizer.record(0.5, 0)
        new = self.sizer.record(0.1, 0)                         # +10%
        self.assertEqual(new, 110)
        self.assertEqual(self.sizer.history[-1].reason, "latency_low")

    def test_deadband_holds(self):
        self.sizer.record(0.52, 0)                               # 0.5*(1±0.1)
        self.assertEqual(self.sizer.size, 100)
        self.assertEqual(self.sizer.history[-1].reason, "hold")

    def test_cooldown_blocks_bounce_back(self):
        self.sizer.record(1.0, 0)                                # 100 -> 50
        self.assertEqual(self.sizer.record(0.01, 0), 50)        # 冷却轮1
        self.assertEqual(self.sizer.history[-1].reason, "cooldown")
        self.assertEqual(self.sizer.record(0.01, 0), 50)        # 冷却轮2
        self.assertEqual(self.sizer.record(0.01, 0), 55)        # 恢复增大

    def test_failure_takes_priority_over_latency(self):
        new = self.sizer.record(1.0, 30)                        # 高耗时+高失败
        self.assertEqual(self.sizer.history[-1].reason, "failure")
        self.assertEqual(new, 50)


class TestBoundsAndValidation(unittest.TestCase):
    def test_clamped_to_min(self):
        s = AdaptiveBatchSizer(min_size=10, max_size=500, initial_size=11)
        s.record(10.0, 0)                                        # 11//2=5 -> 10
        self.assertEqual(s.size, 10)
        self.assertEqual(s.record(10.0, 10), 10)               # 已到下界，记 hold
        self.assertEqual(s.history[-1].reason, "hold")

    def test_clamped_to_max(self):
        s = AdaptiveBatchSizer(min_size=10, max_size=500, initial_size=470)
        for _ in range(10):
            s.record(0.001, 0)
        self.assertEqual(s.size, 500)
        self.assertTrue(all(a.new_size <= 500 for a in s.history))

    def test_initial_size_clamped(self):
        s = AdaptiveBatchSizer(min_size=10, max_size=500, initial_size=9999)
        self.assertEqual(s.size, 500)
        s2 = AdaptiveBatchSizer(min_size=10, max_size=500, initial_size=1)
        self.assertEqual(s2.size, 10)

    def test_invalid_construction(self):
        with self.assertRaises(ValueError):
            AdaptiveBatchSizer(min_size=0)
        with self.assertRaises(ValueError):
            AdaptiveBatchSizer(min_size=100, max_size=10)
        with self.assertRaises(ValueError):
            AdaptiveBatchSizer(decrease_factor=1.0)
        with self.assertRaises(ValueError):
            AdaptiveBatchSizer(deadband=1.0)

    def test_invalid_record(self):
        s = AdaptiveBatchSizer()
        with self.assertRaises(ValueError):
            s.record(-1, 0)
        with self.assertRaises(ValueError):
            s.record(0.1, -1)
        with self.assertRaises(ValueError):
            s.record(0.1, 5, batch_size=4)                      # 失败数 > 批大小
        with self.assertRaises(ValueError):
            s.record(0.1, 0, batch_size=0)

    def test_zero_duration_and_zero_failures(self):
        s = AdaptiveBatchSizer(initial_size=100)
        new = s.record(0.0, 0)                                  # 极快 -> 增大
        self.assertEqual(new, 110)

    def test_all_failures(self):
        s = AdaptiveBatchSizer(initial_size=100)
        for _ in range(6):
            s.record(0.1, s.size)                               # 100% 失败
        self.assertEqual(s.size, 10)                           # 收敛到下界


class TestInjectableTime(unittest.TestCase):
    def test_begin_end_with_fake_clock(self):
        clk = FakeClock()
        s = AdaptiveBatchSizer(initial_size=100, time_fn=clk)
        token = s.begin()
        clk.advance(1.0)
        new = s.end(token, 0)
        self.assertEqual(new, 50)
        self.assertAlmostEqual(s.history[-1].duration, 1.0)

    def test_measure_context_manager(self):
        clk = FakeClock()
        s = AdaptiveBatchSizer(initial_size=100, time_fn=clk)
        holder = {}
        with s.measure(holder) as report:
            clk.advance(0.1)
            report["failures"] = 0
        self.assertEqual(s.size, 110)
        self.assertEqual(holder["failures"], 0)


def _simulate(per_item, failure_rate=lambda r: 0.0, rounds=200,
              initial_size=30, seed=7):
    """确定性工作负载模拟。"""
    rng = random.Random(seed)
    s = AdaptiveBatchSizer(min_size=10, max_size=500, initial_size=initial_size,
                           target_latency=0.5, cooldown_rounds=2)
    for r in range(rounds):
        n = s.size
        duration = n * per_item(r) * rng.uniform(0.97, 1.03)
        failures = int(n * failure_rate(r))
        s.record(duration, failures)
    return s


class TestScenarios(unittest.TestCase):
    def test_stable_load_settles_without_oscillation(self):
        s = _simulate(lambda r: 0.005)
        st = s.stability(window=80)
        # 平衡点约 0.5/0.005 = 100；批大小应在其附近且方向反转极少
        self.assertLess(st["reversals"], 3)
        self.assertGreaterEqual(st["mean_size"], 85)
        self.assertLessEqual(st["mean_size"], 115)
        self.assertLessEqual(st["stddev"], st["mean_size"] * 0.15)

    def test_sudden_slowdown_fast_response(self):
        s = _simulate(lambda r: 0.005 if r < 100 else 0.030,
                      rounds=140, initial_size=100)
        hist = s.history
        target = 0.5
        upper = target * 1.1
        change = 100
        # 变慢前处于稳态
        self.assertLess(hist[change - 1].duration, upper)
        self.assertGreater(hist[change].duration, upper)        # 变慢立刻可见
        recover = None
        for i in range(change, len(hist)):
            if hist[i].duration <= upper:
                recover = i - change + 1
                break
        self.assertIsNotNone(recover, "变慢后应能恢复到目标耗时内")
        # 6 倍变慢，乘性减半：<= 5 轮恢复（量化响应速度）
        self.assertLessEqual(recover, 5, f"恢复耗时 {recover} 轮，过慢")

    def test_persistent_failure_collapses_to_min(self):
        s = _simulate(lambda r: 0.004, failure_rate=lambda r: 0.5,
                      rounds=60, initial_size=200)
        self.assertEqual(s.size, 10)
        st = s.stability(window=20)
        self.assertEqual(st["stddev"], 0.0)                    # 触底后稳态无偏差
        self.assertEqual(st["reversals"], 0)

    def test_load_drop_grows_back_gradually(self):
        s = _simulate(lambda r: 0.020 if r < 100 else 0.001,
                      rounds=240, initial_size=100)
        hist = s.history
        before = hist[99].new_size
        after = hist[-1].new_size
        self.assertLess(before, 40)                             # 高负载时批次小
        self.assertGreater(after, before)                       # 负载下降后回升
        st = s.stability(window=60)
        # 加性增大过程中不应出现“增→减”的来回震荡
        self.assertEqual(st["reversals"], 0)
        self.assertLessEqual(st["mean_size"], 500)


class TestStabilityMetrics(unittest.TestCase):
    def test_empty_history(self):
        s = AdaptiveBatchSizer()
        self.assertEqual(s.stability()["rounds"], 0)

    def test_consecutive_same_direction_counted(self):
        s = AdaptiveBatchSizer(initial_size=10, max_size=10000)
        for _ in range(6):
            s.record(0.001, 0)
        st = s.stability(window=10)
        self.assertEqual(st["max_consecutive"], 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
