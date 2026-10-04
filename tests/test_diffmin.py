"""Self-tests for diffmin (standard-library unittest).

Run from the repository root:
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffmin import (  # noqa: E402
    find_difference,
    minimize,
    minimize_differential,
)


class MinimizerInvariants:
    """Checks shared by every minimization scenario."""

    @staticmethod
    def check(testcase, result, differs):
        # Every accepted round must start from AND land on a differing case.
        for round_record in result.rounds:
            testcase.assertIs(round_record.before_diff, True)
            testcase.assertEqual(
                round_record.after_diff,
                round_record.accepted,
            )
        # Size only shrinks, never grows; accepted rounds shrink by >= 1.
        for round_record in result.rounds:
            testcase.assertLessEqual(round_record.after_size, round_record.before_size)
            if round_record.accepted:
                testcase.assertLess(round_record.after_size, round_record.before_size)
        # The final case really still exhibits the difference.
        testcase.assertTrue(differs(result.minimized))
        # Independent re-verification: deleting any single element kills it.
        for i in range(len(result.minimized)):
            candidate = result.minimized[:i] + result.minimized[i + 1 :]
            testcase.assertFalse(
                differs(candidate),
                msg=f"deleting index {i} still differs: {candidate!r}",
            )
        testcase.assertTrue(
            result.is_minimal_verified,
            msg="minimizer must only claim minimality after verification",
        )


class TestRequiredScenarios(unittest.TestCase):
    def test_already_minimal_single_element(self):
        """一开始就最小：唯一元素不可或缺，零次成功缩减。"""
        differs = lambda case: "a" in case

        result = minimize(differs, ["a"])

        self.assertEqual(result.minimized, ["a"])
        self.assertEqual(result.initial_size, 1)
        self.assertEqual(result.final_size, 1)
        self.assertEqual(len(result.accepted_rounds()), 0)
        self.assertTrue(result.is_minimal_verified)

    def test_already_minimal_multi_element(self):
        """多元素但每个都不可或缺（差异需要 a 与 b 同时存在）。"""
        differs = lambda case: "a" in case and "b" in case

        result = minimize(differs, ["a", "b"])

        self.assertEqual(result.minimized, ["a", "b"])
        self.assertEqual(len(result.accepted_rounds()), 0)
        MinimizerInvariants.check(self, result, differs)

    def test_only_half_can_be_deleted(self):
        """只能删掉一半：8 个元素，恰好保留 4 个才能触发差异。"""
        differs = lambda case: len(case) >= 4 and all(x == 1 for x in case)
        original = [1] * 8

        result = minimize(differs, original)

        self.assertEqual(result.original, original)
        self.assertEqual(result.minimized, [1, 1, 1, 1])
        self.assertEqual(result.initial_size, 8)
        self.assertEqual(result.final_size, 4)
        self.assertEqual(result.initial_size - result.final_size, 4)
        self.assertGreaterEqual(len(result.accepted_rounds()), 1)
        MinimizerInvariants.check(self, result, differs)

    def test_difference_only_at_the_end(self):
        """差异只在末尾：只有最后一个元素是触发点，前面全部可删。"""
        differs = lambda case: bool(case) and case[-1] == "Z"
        original = ["a", "b", "c", "d", "e", "Z"]

        result = minimize(differs, original)

        self.assertEqual(result.minimized, ["Z"])
        self.assertEqual(result.final_size, 1)
        # 被删掉的前缀元素都曾出现在某个被接受的缩减中。
        removed = set()
        current_len = result.initial_size
        for round_record in result.accepted_rounds():
            removed.add(current_len - round_record.after_size)
            current_len = round_record.after_size
        self.assertTrue(any(removed))
        MinimizerInvariants.check(self, result, differs)

    def test_empty_input(self):
        """输入为空且空输入本身就有差异：直接返回空，不得报错或伪造内容。"""
        differs = lambda case: case == []

        result = minimize(differs, [])

        self.assertEqual(result.minimized, [])
        self.assertEqual(result.final_size, 0)
        self.assertEqual(result.rounds, [])
        self.assertTrue(result.is_minimal_verified)

    def test_no_difference_on_original_rejected(self):
        """原始输入无差异时必须如实报错，绝不返回未经验证的“最小用例”。"""
        differs = lambda case: False

        with self.assertRaises(ValueError):
            minimize(differs, [1, 2, 3])

    def test_strategies_combined(self):
        """块删除与单元素删除两种策略都必须出现在过程记录里。"""
        differs = lambda case: len(case) >= 4 and all(x == 1 for x in case)

        result = minimize(differs, [1] * 16)

        strategies = {r.strategy for r in result.rounds}
        self.assertEqual(strategies, {"block", "single"})
        # 每一轮都带缩减前后的差异断言。
        for round_record in result.rounds:
            self.assertIn(round_record.before_diff, (True, False))
            self.assertIn(round_record.after_diff, (True, False))

    def test_iteration_and_size_statistics(self):
        result = minimize(
            lambda case: len(case) >= 4 and all(x == 1 for x in case), [1] * 8
        )
        data = result.to_dict()
        self.assertEqual(data["initial_size"], 8)
        self.assertEqual(data["final_size"], 4)
        self.assertEqual(
            data["total_iterations"],
            data["accepted_iterations"] + data["rejected_iterations"],
        )
        self.assertGreaterEqual(data["difference_checks"], data["total_iterations"])
        self.assertTrue(data["is_minimal_verified"])


class TestDifferentialEndToEnd(unittest.TestCase):
    """两个真实实现：正确的大整数求和 vs 32 位回绕求和。"""

    @staticmethod
    def impl_correct(values):
        return sum(values)

    @staticmethod
    def impl_int32(values):
        total = 0
        for value in values:
            total = (total + value) & 0xFFFFFFFF
        return total - (1 << 32) if total >= (1 << 31) else total

    def test_find_difference_detects_value_mismatch(self):
        diff = find_difference(self.impl_correct, self.impl_int32, [2**31 - 1, 2])
        self.assertIsNotNone(diff)
        self.assertEqual(diff.outcome_a.payload, repr(2**31 + 1))
        self.assertEqual(diff.outcome_b.payload, repr(-2147483647))

    def test_find_difference_no_diff_returns_none(self):
        self.assertIsNone(
            find_difference(self.impl_correct, self.impl_int32, [1, 2, 3])
        )

    def test_exception_is_an_observable_difference(self):
        def raising(values):
            raise ValueError("boom")

        diff = find_difference(self.impl_correct, raising, [1])
        self.assertIsNotNone(diff)
        self.assertEqual(diff.outcome_a.kind, "value")
        self.assertEqual(diff.outcome_b.kind, "exception")

    def test_minimize_differential_finds_minimal_overflow(self):
        original = [3, 1, 4, 2**31 - 1, 9, 2, 7, 0, 5]
        result, difference = minimize_differential(
            self.impl_correct, self.impl_int32, original
        )

        self.assertLess(result.final_size, result.initial_size)
        # 最小反例：2**31-1 加任意一个正数即回绕，恰为 2 个元素。
        self.assertEqual(result.final_size, 2)
        self.assertIn(2**31 - 1, result.minimized)
        self.assertEqual(difference.case, tuple(result.minimized))
        self.assertTrue(result.is_minimal_verified)
        differs = lambda case: find_difference(
            self.impl_correct, self.impl_int32, case
        ) is not None
        MinimizerInvariants.check(self, result, differs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
