"""Self-tests for spin_backoff + the contention simulator.

Run:  python3 -m unittest test_spin_backoff -v
"""

import random
import threading
import unittest

from spin_backoff import (
    BLOCK,
    SLEEP,
    YIELD,
    ExponentialBackoff,
    ExponentialJitterBackoff,
    FixedBackoff,
    SpinLock,
)
from simulate import simulate, default_factories


class FakeLock:
    """Stays busy for `busy_times` acquire calls, then succeeds."""

    def __init__(self, busy_times):
        self.busy_times = busy_times
        self.calls = 0

    def acquire(self, blocking=False):
        self.calls += 1
        return self.calls > self.busy_times

    def release(self):
        pass


class ConstRng:
    """Returns fraction * (b - a) + a from uniform(); 0.0 -> a, 1.0 -> b."""

    def __init__(self, fraction):
        self.fraction = fraction
        self.bounds = []

    def uniform(self, a, b):
        self.bounds.append((a, b))
        return a + self.fraction * (b - a)


class PolicyEscalationTest(unittest.TestCase):
    """Cap reached -> strategy must switch from sleep to yield/block."""

    def test_escalates_to_block_after_max_spins(self):
        p = FixedBackoff(delay=1.0, cap=64.0, max_spins=3, escalate=BLOCK)
        for _ in range(3):
            action, delay = p.step()
            self.assertEqual(action, SLEEP)
            self.assertLessEqual(delay, p.cap)
        action, delay = p.step()
        self.assertEqual(action, BLOCK)   # strategy switch assertion
        self.assertIsNone(delay)
        self.assertEqual(p.escalations, 1)

    def test_escalates_to_yield_when_configured(self):
        p = ExponentialBackoff(base=1.0, cap=64.0, max_spins=2, escalate=YIELD)
        p.step()
        p.step()
        action, _ = p.step()
        self.assertEqual(action, YIELD)   # strategy switch assertion
        self.assertEqual(p.escalations, 1)

    def test_escalation_sticks(self):
        p = FixedBackoff(delay=1.0, max_spins=1, escalate=BLOCK)
        p.step()
        for _ in range(5):
            action, _ = p.step()
            self.assertEqual(action, BLOCK)
        self.assertEqual(p.escalations, 5)

    def test_reset_clears_escalation(self):
        p = FixedBackoff(delay=1.0, max_spins=1, escalate=BLOCK)
        p.step()
        p.step()
        p.reset()
        self.assertEqual(p.escalations, 0)
        action, _ = p.step()
        self.assertEqual(action, SLEEP)

    def test_invalid_escalate_target_rejected(self):
        with self.assertRaises(ValueError):
            FixedBackoff(escalate="spin-forever")


class CapTest(unittest.TestCase):
    def test_exponential_delay_capped(self):
        p = ExponentialBackoff(base=1.0, cap=8.0, max_spins=100)
        delays = [p.step()[1] for _ in range(10)]
        self.assertEqual(delays, [1, 2, 4, 8, 8, 8, 8, 8, 8, 8])
        self.assertTrue(all(d <= p.cap for d in delays))

    def test_fixed_delay_never_exceeds_cap(self):
        p = FixedBackoff(delay=100.0, cap=8.0, max_spins=5)
        for _ in range(5):
            self.assertEqual(p.step()[1], 8.0)


class JitterBoundaryTest(unittest.TestCase):
    def test_delay_within_full_jitter_bounds(self):
        rng = random.Random(42)
        p = ExponentialJitterBackoff(base=1.0, cap=64.0, max_spins=20, rng=rng)
        for spin in range(1, 21):
            action, delay = p.step()
            self.assertEqual(action, SLEEP)
            bound = min(2.0 ** (spin - 1), p.cap)
            self.assertGreaterEqual(delay, 0.0)
            self.assertLessEqual(delay, bound)

    def test_jitter_lower_boundary_zero(self):
        p = ExponentialJitterBackoff(base=2.0, cap=64.0, rng=ConstRng(0.0))
        for _ in range(5):
            self.assertEqual(p.step()[1], 0.0)   # rng -> lower bound

    def test_jitter_upper_boundary_equals_bound(self):
        rng = ConstRng(1.0)
        p = ExponentialJitterBackoff(base=1.0, cap=8.0, max_spins=10, rng=rng)
        delays = [p.step()[1] for _ in range(6)]
        self.assertEqual(delays, [1, 2, 4, 8, 8, 8])  # rng -> upper bound, capped

    def test_jitter_bound_shrinks_to_cap(self):
        rng = ConstRng(0.5)
        p = ExponentialJitterBackoff(base=1.0, cap=8.0, max_spins=10, rng=rng)
        p.step()
        p.step()
        p.step()
        p.step()  # spin 4: raw bound would be 8 == cap
        p.step()  # spin 5: raw bound 16 -> uniform(0, cap=8)
        self.assertEqual(rng.bounds[-1], (0.0, 8.0))


class SpinLockTest(unittest.TestCase):
    def make_lock(self, busy_times):
        sleeps, yields, blocks = [], [], []
        lock = SpinLock(
            lock=FakeLock(busy_times),
            sleep=sleeps.append,
            yield_now=lambda: yields.append(1),
            block=lambda: blocks.append(1),
        )
        return lock, sleeps, yields, blocks

    def test_uncontended_single_attempt(self):
        lock, sleeps, yields, blocks = self.make_lock(busy_times=0)
        lock.acquire(FixedBackoff())
        self.assertEqual(lock.attempts, 1)
        self.assertEqual((sleeps, yields, blocks), ([], [], []))

    def test_switches_from_sleep_to_block_at_cap(self):
        lock, sleeps, yields, blocks = self.make_lock(busy_times=5)
        policy = FixedBackoff(delay=2.0, cap=4.0, max_spins=3, escalate=BLOCK)
        lock.acquire(policy)
        self.assertEqual(lock.attempts, 6)
        self.assertEqual(len(sleeps), 3)          # 3 spins within budget
        self.assertTrue(all(d <= 4.0 for d in sleeps))
        self.assertEqual(len(blocks), 2)          # then switched to blocking
        self.assertEqual(lock.blocks, 2)          # strategy switch assertion

    def test_switches_from_sleep_to_yield_at_cap(self):
        lock, sleeps, yields, blocks = self.make_lock(busy_times=4)
        policy = ExponentialBackoff(base=1.0, cap=4.0, max_spins=2,
                                    escalate=YIELD)
        lock.acquire(policy)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(len(yields), 2)
        self.assertEqual(len(blocks), 0)

    def test_all_three_policies_are_swappable(self):
        for policy in (
            FixedBackoff(delay=1.0, max_spins=4),
            ExponentialBackoff(base=1.0, max_spins=4),
            ExponentialJitterBackoff(base=1.0, max_spins=4,
                                     rng=random.Random(7)),
        ):
            lock, _, _, _ = self.make_lock(busy_times=3)
            lock.acquire(policy)
            self.assertEqual(lock.attempts, 4)

    def test_real_threads_smoke(self):
        lock = SpinLock()
        counter = [0]

        def worker():
            policy = ExponentialJitterBackoff(base=1e-6, cap=1e-3,
                                              max_spins=10,
                                              rng=random.Random())
            for _ in range(2000):
                lock.acquire(policy)
                counter[0] += 1
                lock.release()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(counter[0], 8000)


class SimulationTest(unittest.TestCase):
    def test_no_contention_one_attempt_per_acquire(self):
        for name, factory in default_factories():
            stats = simulate(1, cs_len=10, rounds=50, policy_factory=factory)
            self.assertEqual(stats["total_attempts"], 50, name)
            self.assertEqual(stats["attempts_per_acquire"], 1.0, name)
            self.assertEqual(stats["escalations"], 0, name)

    def test_high_contention_causes_retries_and_escalation(self):
        for name, factory in default_factories():
            stats = simulate(16, cs_len=50, rounds=20, policy_factory=factory)
            self.assertGreater(stats["attempts_per_acquire"], 1.0, name)
            self.assertGreater(stats["escalations"], 0, name)

    def test_exponential_beats_fixed_under_high_contention(self):
        factories = dict(default_factories())
        fixed = simulate(16, 50, 20, factories["fixed(delay=1)"])
        expo = simulate(16, 50, 20, factories["exponential(base=1)"])
        jitter = simulate(16, 50, 20, factories["exp+jitter(base=1)"])
        self.assertLess(expo["total_attempts"], fixed["total_attempts"])
        self.assertLess(jitter["total_attempts"], fixed["total_attempts"])

    def test_every_core_finishes_all_rounds(self):
        stats = simulate(8, cs_len=10, rounds=30,
                         policy_factory=dict(default_factories())["exp+jitter(base=1)"])
        self.assertEqual(stats["rounds"] * stats["threads"], 240)
        self.assertGreaterEqual(stats["total_attempts"], 240)


if __name__ == "__main__":
    unittest.main()
