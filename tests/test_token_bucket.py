"""令牌桶限流器回归测试（仅标准库 unittest，时钟可注入）。

覆盖：单请求、突发流量、长时间运行漂移收敛、时钟回拨、
以及对旧版 int() 截断缺陷的回归守护。

运行：python3 -m unittest discover -s tests -v
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import token_bucket as fixed_mod
import token_bucket_buggy as buggy_mod


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class SingleRequestTests(unittest.TestCase):
    def test_first_request_allowed_and_consumes_token(self):
        clock = FakeClock()
        bucket = fixed_mod.TokenBucket(rate_per_sec=10, capacity=5, clock=clock)
        self.assertTrue(bucket.allow())
        self.assertAlmostEqual(bucket.tokens, 4.0)

    def test_zero_capacity_rejected(self):
        clock = FakeClock()
        bucket = fixed_mod.TokenBucket(rate_per_sec=10, capacity=1, clock=clock)
        self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())
        self.assertAlmostEqual(bucket.tokens, 0.0)

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            fixed_mod.TokenBucket(rate_per_sec=0, capacity=5)
        with self.assertRaises(ValueError):
            fixed_mod.TokenBucket(rate_per_sec=10, capacity=0)

    def test_requests_at_same_timestamp_dont_refill(self):
        clock = FakeClock()
        bucket = fixed_mod.TokenBucket(rate_per_sec=10, capacity=2, clock=clock)
        self.assertTrue(bucket.allow())
        self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())


class BurstTests(unittest.TestCase):
    def test_burst_capped_at_capacity_then_refills_at_rate(self):
        clock = FakeClock()
        rate, cap = 5, 10
        bucket = fixed_mod.TokenBucket(rate, cap, clock=clock)

        burst = [bucket.allow() for _ in range(cap + 5)]
        self.assertEqual(sum(burst), cap)  # 突发量恰好等于容量
        self.assertFalse(bucket.allow())

        clock.advance(1.0)  # 1 秒应精确补充 5 个令牌
        refilled = [bucket.allow() for _ in range(rate + 2)]
        self.assertEqual(sum(refilled), rate)
        self.assertFalse(bucket.allow())

    def test_refill_capped_at_capacity(self):
        clock = FakeClock()
        bucket = fixed_mod.TokenBucket(rate_per_sec=10, capacity=5, clock=clock)
        clock.advance(3600.0)  # 长时间空闲也不能囤积超过容量
        self.assertTrue(bucket.allow())
        self.assertAlmostEqual(bucket.tokens, 4.0)


class LongRunDriftTests(unittest.TestCase):
    def simulate(self, mod, rate, cap, duration, interval):
        clock = FakeClock()
        bucket = mod.TokenBucket(rate, cap, clock=clock)
        allowed = 0
        t = 0.0
        while t < duration:
            clock.t = t
            if bucket.allow():
                allowed += 1
            t += interval
        return allowed

    def test_actual_throughput_converges_to_configured_rate(self):
        rate, cap, duration, interval = 100, 10, 3600, 0.01
        allowed = self.simulate(fixed_mod, rate, cap, duration, interval)
        limit = cap + rate * duration  # 初始容量 + 速率 * 时长
        self.assertLessEqual(allowed, limit + 1)  # 不允许超过配置（+1 仅末拍取整容差）
        self.assertGreaterEqual(allowed, limit - cap)  # 与上限的偏差有界
        self.assertLess(abs(allowed - limit) / limit, 1e-4)  # 相对漂移≈0

    def test_legacy_buggy_version_drifts_as_documented(self):
        # 回归守护：同样的负载下，旧实现必须表现出漂移，
        # 证明本测试装置确实能抓住 int() 截断缺陷。
        rate, cap, duration, interval = 10, 10, 60, 0.001
        allowed_buggy = self.simulate(buggy_mod, rate, cap, duration, interval)
        allowed_fixed = self.simulate(fixed_mod, rate, cap, duration, interval)
        limit = cap + rate * duration
        self.assertGreater(allowed_buggy, limit * 1.5)  # 旧实现明显超标
        self.assertLessEqual(allowed_fixed, limit + 1)


class ClockRewindTests(unittest.TestCase):
    def test_rewind_grants_no_extra_tokens(self):
        clock = FakeClock()
        bucket = fixed_mod.TokenBucket(rate_per_sec=10, capacity=10, clock=clock)

        for _ in range(10):  # 耗尽
            self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())

        clock.advance(0.5)  # 补充 5 个
        for _ in range(5):
            self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())

        clock.t = 0.0  # 时钟大幅回拨：不得产生任何新令牌
        self.assertFalse(bucket.allow())
        self.assertAlmostEqual(bucket.tokens, 0.0)
        clock.advance(0.2)  # 仍在回拨缺口内
        self.assertFalse(bucket.allow())

    def test_forward_progress_after_rewind_resumes_normally(self):
        clock = FakeClock()
        bucket = fixed_mod.TokenBucket(rate_per_sec=10, capacity=10, clock=clock)
        for _ in range(10):
            self.assertTrue(bucket.allow())

        clock.advance(0.5)
        self.assertTrue(bucket.allow())  # 触发补充: +5 消耗 1 -> tokens=4, last=0.5

        clock.t = 0.1  # 回拨：不补充，但已有 4 个令牌仍可用
        self.assertEqual(sum(bucket.allow() for _ in range(10)), 4)

        clock.t = 1.5  # 相对 last=0.5 流逝 1.0s，补充至容量 10
        self.assertEqual(sum(bucket.allow() for _ in range(11)), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
