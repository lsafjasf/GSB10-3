"""Unit tests + randomized differential stress test for timing_wheel.py."""

from __future__ import annotations

import random
import unittest

from timing_wheel import MonotonicClock, TimingWheel, VirtualClock


class TimingWheelTests(unittest.TestCase):
    def test_immediate_fires_in_insertion_order(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        order = []
        for name in "abcde":
            wheel.schedule_after(0, lambda _n, name=name: order.append(name))
        wheel.pump()
        self.assertEqual(order, ["a", "b", "c", "d", "e"])

    def test_pump_does_not_advance_time(self) -> None:
        clock = VirtualClock(7)
        wheel = TimingWheel(clock)
        fired = []
        wheel.schedule_after(0, lambda _n: fired.append(wheel.now))
        wheel.pump()
        self.assertEqual(wheel.now, 7)
        self.assertEqual(fired, [7])

    def test_lazy_cancel_never_fires_and_is_idempotent(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        node = wheel.schedule_after(100, lambda _n: None)
        self.assertTrue(node.cancel())
        self.assertFalse(node.cancel())
        self.assertTrue(node.cancelled)
        self.assertEqual(node.fired, 0)
        wheel.advance(200)
        self.assertEqual(node.fired, 0)

    def test_cancel_already_fired_returns_false(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        node = wheel.schedule_after(0, lambda _n: None)
        wheel.pump()
        self.assertEqual(node.fired, 1)
        self.assertFalse(node.cancel())
        self.assertFalse(node.cancelled)

    def test_cancel_during_cascade(self) -> None:
        # A cancelled node must be physically dropped at cascade time.
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        order = []
        keep = wheel.schedule_at(257, lambda _n: order.append("keep"))
        gone = wheel.schedule_at(300, lambda _n: order.append("gone"))
        gone.cancel()
        wheel.advance(256)  # cascade slot; gone dropped, keep lands in level 0
        wheel.advance(1)
        self.assertEqual(order, ["keep"])
        self.assertEqual(gone.fired, 0)
        self.assertEqual(wheel.pending(), 0)

    def test_same_tick_batch_insertion_order(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        order = []
        for i in range(100):
            wheel.schedule_at(500, lambda _n, i=i: order.append(i))
        wheel.advance(500)
        self.assertEqual(order, list(range(100)))

    def test_mixed_source_same_tick_order(self) -> None:
        # cascaded timers and a later direct insert at the same expiry tick
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        order = []
        wheel.schedule_at(300, lambda _n: order.append(0))
        wheel.schedule_at(300, lambda _n: order.append(1))
        wheel.advance(256)
        wheel.schedule_at(300, lambda _n: order.append(2))
        wheel.advance(44)
        self.assertEqual(order, [0, 1, 2])

    def test_level_chain_boundary_and_off_step(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        edge = wheel.schedule_at(65537, lambda _n: None)
        off = wheel.schedule_at(66048, lambda _n: None)
        self.assertEqual(edge.placements, [2])
        self.assertEqual(off.placements, [2])
        wheel.advance(65537)
        self.assertEqual(edge.placements, [2, 0])
        wheel.advance(66048 - 65537)
        self.assertEqual(off.placements, [2, 1, 0])

    def test_cancel_does_not_disturb_other_nodes_in_slot(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        order = []
        a = wheel.schedule_at(10, lambda _n: order.append("a"))
        b = wheel.schedule_at(10, lambda _n: order.append("b"))
        c = wheel.schedule_at(10, lambda _n: order.append("c"))
        b.cancel()
        wheel.advance(10)
        self.assertEqual(order, ["a", "c"])
        self.assertEqual(b.fired, 0)

    def test_nonzero_start_clock(self) -> None:
        clock = VirtualClock(12345)
        wheel = TimingWheel(clock)
        fired = []
        wheel.schedule_after(256, lambda _n: fired.append(wheel.now))
        wheel.advance(256)
        self.assertEqual(fired, [12345 + 256])

    def test_rejects_backwards_motion(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        with self.assertRaises(ValueError):
            wheel.advance(-1)
        wheel.advance(10)
        with self.assertRaises(ValueError):
            wheel.run_until(9)

    def test_rejects_negative_delay(self) -> None:
        wheel = TimingWheel(TimingWheel(clock=VirtualClock(0))._clock)
        with self.assertRaises(ValueError):
            wheel.schedule_after(-1, lambda _n: None)

    def test_bits_one_small_wheel(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock, bits=1)  # 2 slots/level, max delay 3 ticks
        order = []
        wheel.schedule_after(0, lambda _n: order.append(0))
        wheel.schedule_after(3, lambda _n: order.append(3))
        wheel.pump()
        wheel.advance(3)
        self.assertEqual(order, [0, 3])

    def test_mass_cancel_zero_fire_counts(self) -> None:
        rng = random.Random(99)
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        nodes, cancelled = [], set()
        for i in range(2000):
            expiry = 1 + rng.randrange(5000)
            node = wheel.schedule_at(expiry, lambda _n: None)
            nodes.append(node)
            if rng.random() < 0.8:
                node.cancel()
                cancelled.add(i)
        wheel.advance(5001)
        for i in cancelled:
            self.assertEqual(nodes[i].fired, 0, f"cancelled node {i} fired")
        for i, node in enumerate(nodes):
            if i not in cancelled:
                self.assertEqual(node.fired, 1)

    def test_huge_timeout_bulk_skip(self) -> None:
        clock = VirtualClock(0)
        wheel = TimingWheel(clock)
        hit = []
        wheel.schedule_after(256 ** 4 + 1, lambda _n: hit.append(wheel.now))
        wheel.advance(256 ** 4 + 1)
        self.assertEqual(hit, [256 ** 4 + 1])


class DifferentialTests(unittest.TestCase):
    """Compare the wheel against a trivial reference on random schedules."""

    REFERENCE_BITS = 2  # 4 slots/level -> cascades happen constantly

    def test_random_interleaving_matches_reference(self) -> None:
        for trial in range(20):
            self._run_trial(seed=1000 + trial)

    def _run_trial(self, seed: int) -> None:
        rng = random.Random(seed)
        clock = VirtualClock(0)
        wheel = TimingWheel(clock, bits=self.REFERENCE_BITS)

        # reference: expiry -> list of (seq, alive); events list records order
        ref: dict[int, list] = {}
        seq_to_expiry: dict[int, int] = {}
        seq = 0

        def make_cb(s: int):
            return lambda _n: events.append((wheel.now, s))

        events: list = []
        now = 0
        steps = 400
        for _ in range(steps):
            action = rng.random()
            if action < 0.7:
                expiry = now + rng.randrange(0, 40)
                node = wheel.schedule_at(expiry, make_cb(seq))
                ref.setdefault(expiry, []).append([seq, True, node])
                seq_to_expiry[seq] = expiry
                seq += 1
            elif action < 0.9 and seq_to_expiry:
                # cancel a live timer at random
                choices = [
                    (e, item)
                    for e, items in ref.items()
                    for item in items
                    if item[1] and not item[2].fired
                ]
                if choices:
                    _e, item = rng.choice(choices)
                    item[1] = False
                    self.assertTrue(item[2].cancel())
            else:
                now += rng.randrange(1, 8)
                wheel.run_until(now)

        wheel.run_until(now + 40)

        # build expected (tick, seq) sequence from reference, in expiry order
        expected: list = []
        for expiry in sorted(ref):
            for s, alive, _node in ref[expiry]:
                if alive:
                    expected.append((expiry, s))
        self.assertEqual(events, expected, f"trial seed {seed} mismatch")


class MonotonicClockTests(unittest.TestCase):
    def test_monotonic_clock_advances(self) -> None:
        c = MonotonicClock()
        a, b = c.time(), c.time()
        self.assertIsInstance(a, int)
        self.assertLessEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
