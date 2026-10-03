"""Unit + edge-case tests. Run: python3 -m unittest src.test_adaptive_batch -v
(or: cd src && python3 -m unittest test_adaptive_batch -v)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adaptive_batch import AdaptiveBatchSizer


class FakeClock:
    def __init__(self):
        self.t = 1000.0
    def time(self):
        return self.t


def make(**kw):
    kw.setdefault("min_size", 10)
    kw.setdefault("max_size", 500)
    kw.setdefault("target_latency", 0.2)
    return AdaptiveBatchSizer(**kw)


class TestBounds(unittest.TestCase):
    def test_initial_clamped_to_bounds(self):
        self.assertEqual(make(initial_size=1).batch_size, 10)
        self.assertEqual(make(initial_size=99999).batch_size, 500)

    def test_never_below_min_under_persistent_failure(self):
        s = make()
        for _ in range(50):
            s.record(0.2, failures=s.batch_size)  # 100% failure
        self.assertEqual(s.batch_size, 10)

    def test_never_above_max_under_perfect_conditions(self):
        s = make()
        for _ in range(200):
            s.record(0.001, failures=0)  # super fast
        self.assertEqual(s.batch_size, 500)

    def test_min_equals_max_is_pinned(self):
        s = make(min_size=50, max_size=50)
        for _ in range(10):
            s.record(5.0, failures=40, batch_size=50)
        for _ in range(10):
            s.record(0.0001, failures=0)
        self.assertEqual(s.batch_size, 50)
        self.assertTrue(all(e.new_size == 50 for e in s.events))

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            make(min_size=0)
        with self.assertRaises(ValueError):
            make(min_size=100, max_size=10)
        with self.assertRaises(ValueError):
            make(target_latency=0)
        with self.assertRaises(ValueError):
            make(ema_alpha=0)
        with self.assertRaises(ValueError):
            make(decrease_factor=1.5)

    def test_invalid_record_rejected(self):
        s = make()
        with self.assertRaises(ValueError):
            s.record(-1.0)
        with self.assertRaises(ValueError):
            s.record(0.1, failures=10**9)
        with self.assertRaises(ValueError):
            s.record(0.1, batch_size=0)


class TestAdjustmentBehavior(unittest.TestCase):
    def test_slowdown_shrinks_fast_multiplicatively(self):
        s = make(initial_size=400)
        s.record(0.08)   # warm up EMA within band
        n0 = s.batch_size
        s.record(0.9)    # severe latency
        self.assertLess(s.batch_size, n0 // 2 + 1)
        self.assertEqual(s.events[-1].reason, "slowdown")

    def test_panic_cut_on_extreme_latency(self):
        s = make(initial_size=400)
        s.record(0.2)    # in-band warmup, size unchanged
        s.record(5.0)    # >> panic_ratio * target
        self.assertLessEqual(s.batch_size, int(400 * 0.25) + 1)

    def test_speedup_grows_slowly(self):
        s = make(initial_size=100)
        s.record(0.2)    # EMA warmup at target
        before = s.batch_size
        s.record(0.01)   # way below target
        self.assertEqual(s.batch_size, before + max(1, int(before * 0.1)))

    def test_failures_override_latency(self):
        s = make(initial_size=200)
        for _ in range(3):
            s.record(0.01, failures=int(s.batch_size * 0.5))  # fast but failing
        self.assertLess(s.batch_size, 200)
        self.assertEqual(s.events[-1].reason, "failures")

    def test_deadband_holds_size(self):
        s = make(initial_size=200)
        s.record(0.2)
        n = s.batch_size
        s.record(0.2 * 1.10)   # inside +-15% deadband
        s.record(0.2 * 0.90)
        self.assertEqual(s.batch_size, n)
        self.assertEqual(s.events[-1].reason, "hold")

    def test_cooldown_prevents_immediate_rebound(self):
        s = make(initial_size=400, cooldown_batches=3)
        s.record(0.08)
        s.record(0.9)            # decrease, cooldown armed
        shrunk = s.batch_size
        reasons = [s.record(0.001) or s.batch_size for _ in range(3)]
        self.assertEqual(s.batch_size, shrunk)  # no increase during cooldown
        s.record(0.001)          # cooldown over
        self.assertGreater(s.batch_size, shrunk)

    def test_no_oscillation_on_noisy_steady_load(self):
        s = make(initial_size=200)
        import random
        rng = random.Random(7)
        for _ in range(30):      # let it settle
            s.record(0.2 * (1 + rng.uniform(-0.08, 0.08)))
        sizes = []
        for _ in range(60):
            s.record(0.2 * (1 + rng.uniform(-0.08, 0.08)))
            sizes.append(s.batch_size)
        span = max(sizes) - min(sizes)
        self.assertLessEqual(span, max(sizes) * 0.2)  # bounded steady-state deviation
        dirs = [(b > a) - (b < a) for a, b in zip(sizes, sizes[1:])]
        dirs = [d for d in dirs if d]
        reversals = sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)
        self.assertLessEqual(reversals, 4)


class TestInjectableTime(unittest.TestCase):
    def test_events_use_injected_clock(self):
        clock = FakeClock()
        s = make(now=clock.time)
        s.record(0.2)
        clock.t += 5
        s.record(0.2)
        self.assertEqual(s.events[0].at, 1000.0)
        self.assertEqual(s.events[1].at, 1005.0)

    def test_default_clock_is_monotonic(self):
        s = AdaptiveBatchSizer(min_size=1, max_size=2)
        s.record(0.1)
        self.assertGreater(s.events[0].at, 0)


class TestObservability(unittest.TestCase):
    def test_every_record_produces_before_after_event(self):
        s = make(initial_size=100)
        s.record(0.5)
        e = s.events[-1]
        self.assertEqual((e.old_size, e.new_size), (100, s.batch_size))
        self.assertIn(e.reason, ("slowdown", "failures", "speedup", "hold"))

    def test_zero_latency_batch_grows(self):
        s = make(initial_size=100)
        s.record(0.0)
        self.assertGreaterEqual(s.batch_size, 100)


if __name__ == "__main__":
    unittest.main()
