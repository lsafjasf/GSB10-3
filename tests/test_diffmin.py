import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffmin import (
    DifferentialHarness,
    MinimizationError,
    NoDifferenceError,
    compare,
    minimize,
)


def ref_sum(xs):
    return sum(xs)


def buggy_sum_skip_first(xs):
    total = 0
    for index, value in enumerate(xs):
        if index == 0:
            continue
        total += value
    return total


def buggy_sum_tail(xs):
    total = sum(xs)
    if len(xs) > 0 and xs[-1] == 99:
        total += 1000
    return total


def buggy_sum_empty(xs):
    if len(xs) == 0:
        raise ValueError("empty input not supported")
    return sum(xs)


def buggy_sum_pair(xs):
    total = sum(xs)
    if 1 in xs and 2 in xs:
        total += 100
    return total


class TestHarness(unittest.TestCase):
    def test_equal_results_not_different(self):
        harness = DifferentialHarness(ref_sum, lambda xs: sum(xs))
        self.assertFalse(harness.differs([1, 2, 3]))
        self.assertFalse(harness.differs([]))

    def test_value_difference_detected(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_skip_first)
        self.assertTrue(harness.differs([5, 6]))

    def test_value_vs_exception_is_different(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_empty)
        result = harness.compare([])
        self.assertTrue(result.different)
        self.assertEqual(result.reference.kind, "value")
        self.assertEqual(result.candidate.kind, "error")

    def test_same_exception_not_different(self):
        def boom_a(xs):
            raise ValueError("nope")

        def boom_b(xs):
            raise ValueError("nope")

        harness = DifferentialHarness(boom_a, boom_b)
        self.assertFalse(harness.differs([1]))

    def test_discrepancies_collection(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_tail)
        found = harness.discrepancies([[1], [99], [2, 3], [7, 99]])
        self.assertEqual([r.input for r in found], [[99], [7, 99]])

    def test_compare_convenience(self):
        result = compare(ref_sum, buggy_sum_tail, [99])
        self.assertTrue(result.different)
        self.assertIn("different  = True", result.describe())


class TestMinimizeRequiredCases(unittest.TestCase):
    def test_already_minimal_input(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_skip_first)
        report = minimize(harness, [7])
        self.assertEqual(report.status, "minimal")
        self.assertTrue(report.verified)
        self.assertEqual(report.result, [7])
        self.assertEqual(report.iterations, 0)
        self.assertEqual(report.original_size, 1)
        self.assertEqual(report.final_size, 1)
        self.assertEqual(len(report.singleton_checks), 1)
        self.assertTrue(report.singleton_checks[0].kept)

    def test_only_half_can_be_removed(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_pair)
        report = minimize(harness, [1, 2, 3, 4])
        self.assertEqual(sorted(report.result), [1, 2])
        self.assertEqual(report.final_size, 2)
        self.assertEqual(report.original_size, 4)
        self.assertGreaterEqual(report.iterations, 1)
        for rnd in report.rounds:
            self.assertTrue(rnd.before.different)
            self.assertTrue(rnd.after.different)
            self.assertLess(len(rnd.after.input), len(rnd.before.input))

    def test_difference_only_at_tail(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_tail)
        original = list(range(50)) + [99]
        report = minimize(harness, original)
        self.assertEqual(report.result, [99])
        self.assertEqual(report.final_size, 1)
        self.assertEqual(report.original_size, 51)
        self.assertTrue(report.verified)

    def test_empty_input_diverges(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_empty)
        report = minimize(harness, [])
        self.assertEqual(report.status, "minimal")
        self.assertTrue(report.verified)
        self.assertEqual(report.result, [])
        self.assertEqual(report.iterations, 0)
        self.assertEqual(report.final_size, 0)
        self.assertEqual(report.singleton_checks, [])

    def test_empty_input_no_divergence(self):
        harness = DifferentialHarness(ref_sum, lambda xs: sum(xs))
        with self.assertRaises(NoDifferenceError):
            minimize(harness, [])

    def test_no_divergence_nonempty(self):
        harness = DifferentialHarness(ref_sum, lambda xs: sum(xs))
        with self.assertRaises(NoDifferenceError):
            minimize(harness, [1, 2, 3])


class TestMinimizeHonesty(unittest.TestCase):
    def test_unverified_result_is_never_returned(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_skip_first)
        report = minimize(harness, [3, 1, 4, 1, 5])
        self.assertEqual(report.result, [5])

        honest = DifferentialHarness(ref_sum, ref_sum)
        with self.assertRaises(MinimizationError):
            report.verify(honest)

    def test_every_round_preserves_divergence(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_tail)
        report = minimize(harness, list(range(30)) + [99])
        self.assertGreater(len(report.rounds), 0)
        for rnd in report.rounds:
            self.assertTrue(rnd.before.different)
            self.assertTrue(rnd.after.different)
            self.assertTrue(harness.differs(list(rnd.before.input)))
            self.assertTrue(harness.differs(list(rnd.after.input)))

    def test_report_serializes_to_json(self):
        harness = DifferentialHarness(ref_sum, buggy_sum_tail)
        report = minimize(harness, [1, 2, 99])
        data = json.loads(report.to_json())
        self.assertEqual(data["status"], "minimal")
        self.assertEqual(data["result"], [99])
        self.assertEqual(data["original_size"], 3)
        self.assertEqual(data["final_size"], 1)
        self.assertIn("iterations", data)
        self.assertIn("trials", data)
        self.assertTrue(data["verified"])

    def test_seeded_fuzz_minimality(self):
        rng = random.Random(20261004)
        for _ in range(25):
            length = rng.randint(1, 40)
            original = [rng.randint(0, 9) for _ in range(length)] + [99]
            rng.shuffle(original)
            if original[-1] != 99:
                original.append(99)
            harness = DifferentialHarness(ref_sum, buggy_sum_tail)
            report = minimize(harness, original)
            self.assertEqual(report.result, [99])
            self.assertTrue(report.verified)


if __name__ == "__main__":
    unittest.main()
