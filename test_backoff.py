"""自测：策略切换断言、抖动边界、无竞争、高竞争、互斥正确性。

运行：python3 -m unittest test_backoff -v
"""

import threading
import time
import unittest

from backoff import (
    Action,
    ExponentialBackoff,
    ExponentialJitterBackoff,
    FixedBackoff,
    SpinLock,
)
from simulate import make_factories, simulate


class FixedBackoffTest(unittest.TestCase):
    def test_spins_then_switch_to_yield(self):
        s = FixedBackoff(spins=3, then=Action.YIELD)
        for attempt in (1, 2, 3):
            self.assertIs(s.decide(attempt).action, Action.SPIN)
        # 策略切换断言：超过固定次数后必须转为让出
        for attempt in (4, 5, 100):
            self.assertIs(s.decide(attempt).action, Action.YIELD)

    def test_spins_then_switch_to_sleep(self):
        s = FixedBackoff(spins=2, then=Action.SLEEP, block_delay=0.5)
        self.assertIs(s.decide(1).action, Action.SPIN)
        d = s.decide(3)
        self.assertIs(d.action, Action.SLEEP)
        self.assertEqual(d.delay, 0.5)

    def test_zero_spins_switches_immediately(self):
        s = FixedBackoff(spins=0, then=Action.YIELD)
        self.assertIs(s.decide(1).action, Action.YIELD)


class ExponentialBackoffTest(unittest.TestCase):
    def test_growth_and_cap_switch(self):
        s = ExponentialBackoff(base=1e-6, factor=10.0, cap=1e-3, then=Action.YIELD)
        expected = [1e-6, 1e-5, 1e-4, 1e-3]
        for i, delay in enumerate(expected, start=1):
            d = s.decide(i)
            self.assertIs(d.action, Action.SLEEP)
            self.assertAlmostEqual(d.delay, delay)
        # 策略切换断言：1e-2 > cap，第 5 次起必须转为让出
        for attempt in (5, 6, 50):
            self.assertIs(s.decide(attempt).action, Action.YIELD)

    def test_delay_never_exceeds_cap(self):
        s = ExponentialBackoff(base=1e-6, factor=2.0, cap=1e-3, then=Action.SLEEP)
        for attempt in range(1, 100):
            self.assertLessEqual(s.decide(attempt).delay, 1e-3)

    def test_sleep_fallback_uses_cap(self):
        s = ExponentialBackoff(base=1e-6, factor=10.0, cap=1e-3, then=Action.SLEEP)
        d = s.decide(10)
        self.assertIs(d.action, Action.SLEEP)
        self.assertEqual(d.delay, 1e-3)


class JitterBoundaryTest(unittest.TestCase):
    def test_random_zero_gives_zero_delay(self):
        s = ExponentialJitterBackoff(base=1e-6, factor=2.0, cap=1e-3,
                                     random_fn=lambda: 0.0)
        for attempt in (1, 2, 5):
            d = s.decide(attempt)
            self.assertIs(d.action, Action.SLEEP)
            self.assertEqual(d.delay, 0.0)

    def test_random_one_gives_full_exponential_delay(self):
        s = ExponentialJitterBackoff(base=1e-6, factor=2.0, cap=1e-3,
                                     random_fn=lambda: 1.0)
        self.assertAlmostEqual(s.decide(1).delay, 1e-6)
        self.assertAlmostEqual(s.decide(2).delay, 2e-6)
        self.assertAlmostEqual(s.decide(10).delay, 512e-6)

    def test_delay_always_within_bounds(self):
        import random
        rng = random.Random(1234)
        s = ExponentialJitterBackoff(base=1e-6, factor=2.0, cap=1e-3,
                                     random_fn=rng.random)
        for attempt in range(1, 12):
            raw = 1e-6 * 2.0 ** (attempt - 1)
            if raw <= 1e-3:
                for _ in range(200):
                    d = s.decide(attempt)
                    self.assertIs(d.action, Action.SLEEP)
                    self.assertGreaterEqual(d.delay, 0.0)
                    self.assertLessEqual(d.delay, raw)

    def test_switch_ignores_random_value(self):
        # 超过 cap 后无论随机数取边界 0 还是 1，都必须切换为让出
        for value in (0.0, 0.5, 1.0):
            s = ExponentialJitterBackoff(base=1e-6, factor=2.0, cap=1e-3,
                                         then=Action.YIELD,
                                         random_fn=lambda: value)
            self.assertIs(s.decide(11).action, Action.YIELD)  # 2**10 * 1e-6 > 1e-3


class SpinLockTest(unittest.TestCase):
    def test_no_contention_single_attempt(self):
        calls = []

        class Probe(FixedBackoff):
            def decide(self, attempt):
                calls.append(attempt)
                return super().decide(attempt)

        lock = SpinLock(Probe(spins=4), sleep_fn=lambda d: None, yield_fn=lambda: None)
        attempts = lock.acquire()
        lock.release()
        self.assertEqual(attempts, 1)          # 无竞争：一次成功
        self.assertEqual(calls, [])            # 无竞争：策略根本不该被触发

    def test_injected_clock_records_sleeps(self):
        slept = []
        yielded = []
        lock = SpinLock(FixedBackoff(spins=2, then=Action.SLEEP, block_delay=0.25),
                        sleep_fn=slept.append, yield_fn=lambda: yielded.append(1))
        lock.acquire()
        done = []

        def worker():
            done.append(lock.acquire())
            lock.release()

        t = threading.Thread(target=worker)
        t.start()
        while len(slept) < 3:
            pass  # 等 worker 至少切换进阻塞退路 3 次
        lock.release()
        t.join()
        self.assertGreater(done[0], 1)         # 有竞争：尝试次数 > 1
        self.assertTrue(all(d == 0.25 for d in slept))  # 注入时钟收到固定阻塞时长

    def test_high_contention_mutual_exclusion(self):
        lock = SpinLock(FixedBackoff(spins=8, then=Action.YIELD))
        counter = 0
        max_holders = 0
        per_thread_attempts = []

        def work():
            nonlocal counter, max_holders
            total = 0
            for _ in range(500):
                n = lock.acquire()
                total += n
                max_holders = max(max_holders, lock.holder_count)
                tmp = counter
                # 忙等约 50us 拉长临界区，制造真实竞争窗口
                deadline = time.perf_counter() + 50e-6
                while time.perf_counter() < deadline:
                    pass
                counter = tmp + 1
                lock.release()
            return total

        threads = [threading.Thread(target=lambda: per_thread_attempts.append(work()))
                   for _ in range(8)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(counter, 8 * 500)     # 互斥正确：无丢失更新
        self.assertLessEqual(max_holders, 1)   # 任意时刻至多一个持有者
        self.assertGreater(sum(per_thread_attempts), 8 * 500)  # 高竞争：总尝试 > 获取次数


class SimulationTest(unittest.TestCase):
    def test_no_contention_simulation(self):
        total, failed, _ = simulate(make_factories(0)["fixed"], cores=1,
                                    cs_len=1e-6, iterations=100)
        self.assertEqual(total, 100)           # 单核：每次一把过
        self.assertEqual(failed, 0)

    def test_high_contention_simulation(self):
        for name, factory in make_factories(0).items():
            total, failed, _ = simulate(factory, cores=16, cs_len=1e-5,
                                        iterations=50, seed=7)
            self.assertEqual(total - failed, 16 * 50)  # 成功次数恒等于请求次数
            self.assertGreater(failed, 16 * 50)        # 高竞争下失败次数超过成功次数

    def test_deterministic_with_seed(self):
        a = simulate(make_factories(9)["exponential_jitter"], 8, 1e-5, 30, seed=9)
        b = simulate(make_factories(9)["exponential_jitter"], 8, 1e-5, 30, seed=9)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
