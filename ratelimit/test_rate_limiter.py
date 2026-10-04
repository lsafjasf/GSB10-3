"""TokenBucket 回归测试（标准库 unittest，时钟全部注入 FakeClock）。

运行: python3 -m unittest test_rate_limiter -v
"""

import threading
import unittest

from fake_clock import FakeClock
from rate_limiter import TokenBucket


class TestSingleRequest(unittest.TestCase):
    def test_first_request_allowed(self):
        clock = FakeClock()
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        self.assertTrue(bucket.allow())

    def test_empty_bucket_rejects(self):
        clock = FakeClock()
        bucket = TokenBucket(rate=100, capacity=1, clock=clock)
        self.assertTrue(bucket.allow())   # 消耗唯一令牌
        self.assertFalse(bucket.allow())  # 时间未前进，无补充，必须拒绝

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            TokenBucket(rate=0, capacity=10, clock=FakeClock())
        with self.assertRaises(ValueError):
            TokenBucket(rate=100, capacity=0, clock=FakeClock())


class TestBurst(unittest.TestCase):
    def test_burst_up_to_capacity(self):
        clock = FakeClock()
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        results = [bucket.allow() for _ in range(11)]
        self.assertEqual(results.count(True), 10)   # 突发最多打满容量
        self.assertEqual(results.count(False), 1)   # 第 11 个必须拒绝

    def test_burst_refills_after_idle(self):
        clock = FakeClock()
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        for _ in range(10):
            self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())
        clock.advance(1.0)  # 空闲 1s，应补满 100 个但容量封顶 10
        results = [bucket.allow() for _ in range(11)]
        self.assertEqual(results.count(True), 10)

    def test_no_refill_without_time_advance(self):
        clock = FakeClock()
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        for _ in range(10):
            bucket.allow()
        # 同一时刻反复调用不得产生新令牌（回归：取整/重复补充）
        for _ in range(100):
            self.assertFalse(bucket.allow())


class TestFractionalRefill(unittest.TestCase):
    def test_fractional_tokens_accumulate(self):
        """回归核心：rate=100/s 时 1ms 只产生 0.1 个令牌，
        连续 9 次 1ms 间隔请求必须全部拒绝，第 10ms 才凑够 1 个。"""
        clock = FakeClock()
        bucket = TokenBucket(rate=100, capacity=1, clock=clock)
        self.assertTrue(bucket.allow())  # 用掉初始令牌
        for _ in range(9):
            clock.advance(0.001)
            self.assertFalse(bucket.allow())
        clock.advance(0.001)  # 累计 10ms = 1 个令牌
        self.assertTrue(bucket.allow())


class TestLongRun(unittest.TestCase):
    def test_long_run_converges_to_limit(self):
        """模拟 600s、1ms 间隔共 60 万请求：
        通过量必须收敛到 capacity + rate*duration（容差 ±2）。"""
        clock = FakeClock()
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        allowed = 0
        for _ in range(600_000):
            if bucket.allow():
                allowed += 1
            clock.advance(0.001)
        expected = 10 + 100 * 600
        self.assertAlmostEqual(allowed, expected, delta=2)

    def test_long_run_never_exceeds_limit(self):
        """多种请求间隔下，通过量都不得超过理论上限（容差 ±2）。"""
        for interval in (0.0005, 0.001, 0.003, 0.007, 0.01):
            clock = FakeClock()
            bucket = TokenBucket(rate=100, capacity=10, clock=clock)
            allowed = 0
            steps = int(120 / interval)
            for _ in range(steps):
                if bucket.allow():
                    allowed += 1
                clock.advance(interval)
            # 到达速率低于等于限速时全部通过；高于限速时收敛到上限
            expected = min(steps, 10 + 100 * 120)
            self.assertLessEqual(allowed, 10 + 100 * 120 + 2,
                                 msg=f"interval={interval}")
            self.assertAlmostEqual(allowed, expected, delta=2,
                                   msg=f"interval={interval}")


class TestClockRewind(unittest.TestCase):
    def test_rewind_grants_no_extra_tokens(self):
        clock = FakeClock(start=1000.0)
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        for _ in range(10):
            bucket.allow()  # 打空
        clock.set(500.0)  # 回拨 500s
        for _ in range(100):
            self.assertFalse(bucket.allow())  # 回拨不得白送令牌

    def test_no_double_credit_after_recovery(self):
        """回拨再恢复后，不得对同一段时间重复发放令牌。"""
        clock = FakeClock(start=1000.0)
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        for _ in range(10):
            bucket.allow()
        clock.set(500.0)          # 回拨到 500
        clock.set(1000.0)         # 恢复到回拨前
        self.assertFalse(bucket.allow())  # 这段时间已结算过，不能再发
        clock.advance(0.1)        # 真实前进 0.1s = 10 个令牌
        results = [bucket.allow() for _ in range(11)]
        self.assertEqual(results.count(True), 10)

    def test_rewind_does_not_move_last_refill_backwards(self):
        clock = FakeClock(start=1000.0)
        bucket = TokenBucket(rate=100, capacity=10, clock=clock)
        bucket.allow()
        clock.set(0.0)
        bucket.allow()
        self.assertEqual(bucket.last_refill, 1000.0)


class TestConcurrency(unittest.TestCase):
    def test_concurrent_allow_never_exceeds_limit(self):
        """多线程并发取令牌，总数不得超过桶内真实令牌数。"""
        clock = FakeClock()
        bucket = TokenBucket(rate=1000, capacity=100, clock=clock)
        allowed = []
        lock = threading.Lock()

        def worker():
            local = 0
            for _ in range(1000):
                if bucket.allow():
                    local += 1
            with lock:
                allowed.append(local)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 时间未前进，只有初始 100 个令牌可发
        self.assertEqual(sum(allowed), 100)


if __name__ == "__main__":
    unittest.main()
