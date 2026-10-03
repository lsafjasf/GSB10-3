"""Self-tests for weighted_fair_scheduler. Run: python3 test_weighted_fair_scheduler.py -v"""

import random
import unittest
from collections import Counter
from fractions import Fraction

from weighted_fair_scheduler import WeightedFairScheduler


def make_scheduler(weights):
    sched = WeightedFairScheduler()
    for name, weight in weights:
        sched.add_class(name, weight)
    return sched


def fill(sched, amounts):
    """Enqueue ``amounts[name]`` items tagged (name, seq) into each class."""
    for name, count in amounts.items():
        for seq in range(count):
            sched.enqueue(name, (name, seq))


def drain(sched, limit=None):
    """Dispatch until empty (or ``limit`` times); return list of (name, item)."""
    out = []
    while limit is None or len(out) < limit:
        result = sched.dispatch()
        if result is None:
            break
        out.append(result)
    return out


class TestSingleClass(unittest.TestCase):
    def test_single_class_gets_everything_in_fifo_order(self):
        sched = make_scheduler([("only", 5)])
        fill(sched, {"only": 7})
        out = drain(sched)
        self.assertEqual([item for _, item in out],
                         [("only", i) for i in range(7)])
        self.assertEqual(sched.served_counts(), {"only": 7})

    def test_single_class_weight_value_is_irrelevant(self):
        for weight in (1, 3, 1000):
            sched = make_scheduler([("only", weight)])
            fill(sched, {"only": 4})
            self.assertEqual(len(drain(sched)), 4)


class TestProportionalFairness(unittest.TestCase):
    def test_long_run_shares_match_weights(self):
        weights = [("high", 8), ("mid", 3), ("low", 1)]
        sched = make_scheduler(weights)
        fill(sched, {"high": 100_000, "mid": 100_000, "low": 100_000})
        out = drain(sched, limit=12_000)
        counts = Counter(name for name, _ in out)
        total = sum(counts.values())
        for name, weight in weights:
            expected = weight / 12
            actual = counts[name] / total
            self.assertAlmostEqual(actual, expected, delta=0.01)

    def test_bounded_discrepancy_at_every_prefix(self):
        # Stride scheduling keeps each class within a small constant of its
        # ideal share at *every* point in time, not just on average.
        weights = {"high": 8, "mid": 3, "low": 1}
        sched = make_scheduler(list(weights.items()))
        fill(sched, {n: 50_000 for n in weights})
        total_weight = sum(weights.values())
        served = Counter()
        for step in range(1, 20_001):
            name, _ = sched.dispatch()
            served[name] += 1
            for other, weight in weights.items():
                ideal = step * weight / total_weight
                self.assertLessEqual(
                    abs(served[other] - ideal), 2,
                    f"step {step}: class {other} off ideal by "
                    f"{abs(served[other] - ideal)}")


class TestNoStarvationUnderPressure(unittest.TestCase):
    SCENARIO_TICKS = 50_000  # deterministic arrival process length

    def _run_pressure_scenario(self):
        """High-priority traffic never lets up; low-priority trickles in."""
        sched = make_scheduler([("high", 100), ("low", 1)])
        low_arrivals = 0
        served = Counter()
        for tick in range(self.SCENARIO_TICKS):
            # High priority is *always* backlogged (sustained pressure).
            sched.enqueue("high", ("high", tick))
            # Low priority is also kept continuously backlogged, so its
            # service rate is purely scheduler-driven (worst case).
            sched.enqueue("low", ("low", low_arrivals))
            low_arrivals += 1
            name, _ = sched.dispatch()
            served[name] += 1
        return sched, served, low_arrivals

    def test_low_priority_is_served_under_sustained_pressure(self):
        sched, served, low_arrivals = self._run_pressure_scenario()
        # Fair share of "low" is 1/101 of 50000 ~ 495.
        self.assertGreaterEqual(served["low"], 400)
        # Ratio stays near the configured 100:1 weights.
        ratio = served["high"] / served["low"]
        self.assertTrue(90 <= ratio <= 110, f"ratio={ratio}")
        # Once pressure stops, every queued low-priority item drains.
        drain(sched)
        self.assertEqual(sched.served_counts()["low"], low_arrivals)

    def test_max_gap_between_low_priority_services(self):
        sched = make_scheduler([("high", 100), ("low", 1)])
        # Keep one low item perpetually pending while high floods in.
        gaps = []
        last_low_step = None
        sched.enqueue("low", ("low", 0))
        high_seq = 0
        for step in range(30_000):
            sched.enqueue("high", ("high", high_seq))
            high_seq += 1
            name, _ = sched.dispatch()
            if name == "low":
                if last_low_step is not None:
                    gaps.append(step - last_low_step)
                last_low_step = step
                sched.enqueue("low", ("low", len(gaps)))  # re-arm
        # With weights 100:1, low should be served roughly every 101 steps.
        self.assertTrue(gaps, "low priority was never served")
        self.assertLessEqual(max(gaps), 103)
        self.assertGreaterEqual(min(gaps), 99)


class TestZeroWeight(unittest.TestCase):
    def test_zero_weight_served_only_when_no_positive_backlog(self):
        sched = make_scheduler([("real", 1), ("best", 0)])
        fill(sched, {"real": 3, "best": 5})
        order = [name for name, _ in drain(sched)]
        # All zero-weight items come after every positive-weight item.
        self.assertEqual(order, ["real"] * 3 + ["best"] * 5)

    def test_zero_weight_never_served_under_sustained_pressure(self):
        sched = make_scheduler([("high", 1), ("best", 0)])
        sched.enqueue("best", ("best", 0))
        for step in range(1_000):
            sched.enqueue("high", ("high", step))
            name, _ = sched.dispatch()
            self.assertEqual(name, "high")
        self.assertEqual(sched.served_counts()["best"], 0)
        self.assertEqual(sched.pending("best"), 1)

    def test_zero_weight_classes_round_robin_between_themselves(self):
        sched = make_scheduler([("za", 0), ("zb", 0)])
        fill(sched, {"za": 3, "zb": 3})
        order = [name for name, _ in drain(sched)]
        self.assertEqual(order, ["za", "zb"] * 3)

    def test_zero_weight_fifo_within_class(self):
        sched = make_scheduler([("best", 0)])
        fill(sched, {"best": 4})
        out = drain(sched)
        self.assertEqual([item for _, item in out],
                         [("best", i) for i in range(4)])

    def test_zero_weight_resumes_after_pressure_releases(self):
        sched = make_scheduler([("high", 10), ("best", 0)])
        fill(sched, {"high": 20, "best": 2})
        first = [name for name, _ in drain(sched, limit=20)]
        self.assertEqual(first, ["high"] * 20)
        rest = [name for name, _ in drain(sched)]
        self.assertEqual(rest, ["best", "best"])


class TestDeterminism(unittest.TestCase):
    def _scripted_run(self):
        sched = make_scheduler([("a", 5), ("b", 3), ("c", 0), ("d", 1)])
        rng = random.Random(42)
        names = ["a", "b", "c", "d"]
        out = []
        for step in range(5_000):
            for _ in range(rng.randint(0, 3)):
                name = rng.choice(names)
                sched.enqueue(name, (name, step))
            result = sched.dispatch()
            if result is not None:
                out.append(result)
        return out

    def test_identical_inputs_identical_outputs(self):
        first = self._scripted_run()
        second = self._scripted_run()
        self.assertEqual(first, second)
        self.assertGreater(len(first), 0)

    def test_tie_break_follows_registration_order(self):
        # Fresh classes all start at pass 0; ties must resolve by add order.
        sched = make_scheduler([("x", 1), ("y", 1), ("z", 1)])
        fill(sched, {"x": 1, "y": 1, "z": 1})
        order = [name for name, _ in drain(sched, limit=3)]
        self.assertEqual(order, ["x", "y", "z"])


class TestEdgeCases(unittest.TestCase):
    def test_dispatch_on_empty_scheduler_returns_none(self):
        sched = WeightedFairScheduler()
        self.assertIsNone(sched.dispatch())

    def test_dispatch_when_all_queues_empty_returns_none(self):
        sched = make_scheduler([("a", 1), ("b", 0)])
        self.assertIsNone(sched.dispatch())

    def test_negative_weight_rejected(self):
        sched = WeightedFairScheduler()
        with self.assertRaises(ValueError):
            sched.add_class("bad", -1)

    def test_non_integer_weight_rejected(self):
        sched = WeightedFairScheduler()
        for bad in (1.5, "2", True, None):
            with self.assertRaises((TypeError, ValueError)):
                sched.add_class("bad", bad)

    def test_duplicate_class_rejected(self):
        sched = make_scheduler([("a", 1)])
        with self.assertRaises(ValueError):
            sched.add_class("a", 2)

    def test_enqueue_unknown_class_rejected(self):
        sched = make_scheduler([("a", 1)])
        with self.assertRaises(KeyError):
            sched.enqueue("nope", "item")

    def test_reactivation_does_not_starve_or_burst(self):
        # Class "rare" goes idle, then returns while "busy" kept running.
        # Equal weights: it must rejoin promptly and then alternate 1:1,
        # with no catch-up burst for the time it was idle.
        sched = make_scheduler([("busy", 1), ("rare", 1)])
        fill(sched, {"busy": 10_000})
        drain(sched, limit=5_000)  # busy runs alone for a while
        fill(sched, {"rare": 2})
        out = drain(sched, limit=4)
        names = [name for name, _ in out]
        self.assertEqual(names, ["rare", "busy", "rare", "busy"])


class TestFairnessReport(unittest.TestCase):
    """Prints the fairness data table (also written to fairness_report.md)."""

    def test_generate_report(self):
        lines = []
        lines.append("# Fairness Data\n")
        lines.append("Generated by `python3 test_weighted_fair_scheduler.py "
                     "TestFairnessReport` (deterministic, stdlib only).\n")

        # Scenario 1: three classes, weights 8:3:1, all saturated.
        weights = [("high", 8), ("mid", 3), ("low", 1)]
        sched = make_scheduler(weights)
        fill(sched, {n: 100_000 for n, _ in weights})
        out = drain(sched, limit=12_000)
        counts = Counter(name for name, _ in out)
        total = sum(counts.values())
        lines.append("\n## Scenario 1: saturated classes, weights 8:3:1, "
                     f"{total} dispatches\n")
        lines.append("| class | weight | served | actual share | "
                     "ideal share | deviation |")
        lines.append("|---|---|---|---|---|---|")
        for name, weight in weights:
            actual = counts[name] / total
            ideal = weight / 12
            lines.append(f"| {name} | {weight} | {counts[name]} | "
                         f"{actual:.4f} | {ideal:.4f} | "
                         f"{actual - ideal:+.4f} |")
        for name, weight in weights:
            self.assertAlmostEqual(counts[name] / total, weight / 12,
                                   delta=0.01)

        # Scenario 2: sustained high-priority pressure, weights 100:1.
        sched = make_scheduler([("high", 100), ("low", 1)])
        low_arrivals = 0
        served = Counter()
        checkpoints = {10_000, 25_000, 50_000}
        rows = []
        for step in range(1, 50_001):
            sched.enqueue("high", ("high", step))
            sched.enqueue("low", ("low", low_arrivals))
            low_arrivals += 1
            name, _ = sched.dispatch()
            served[name] += 1
            if step in checkpoints:
                rows.append((step, served["high"], served["low"]))
        lines.append("\n## Scenario 2: sustained high-priority pressure, "
                     "weights 100:1\n")
        lines.append("Both classes are backlogged at every one of the "
                     "50,000 ticks (worst-case continuous pressure on the "
                     "weight-1 class).\n")
        lines.append("| tick | high served | low served | low share | "
                     "ideal low share |")
        lines.append("|---|---|---|---|---|")
        for step, high, low in rows:
            lines.append(f"| {step} | {high} | {low} | "
                         f"{low / step:.4f} | {1 / 101:.4f} |")
        drain(sched)  # pressure stops; backlog must drain completely
        drained = sched.served_counts()["low"] == low_arrivals
        lines.append(f"\nLow-priority items enqueued: {low_arrivals}; "
                     f"served during pressure: {served['low']}; "
                     f"all served after pressure stops: {drained}. "
                     "No starvation at any point: the weight-1 class "
                     "receives ~1/101 of service throughout.\n")
        self.assertTrue(drained)
        self.assertGreaterEqual(served["low"], 400)

        report = "\n".join(lines) + "\n"
        with open("fairness_report.md", "w", encoding="utf-8") as fh:
            fh.write(report)
        print("\n" + report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
