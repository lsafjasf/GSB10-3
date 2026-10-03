"""两阶段批量执行器自测（标准库 unittest）。

覆盖：
  1. 全部成功
  2. 准备失败 -> 立即中止 + 逆序回退
  3. 提交失败 -> 已提交项无法回退被记录，部分成功显式标记
  4. 回退本身失败 -> 整体结论为脏状态
  5. 幂等契约断言（重复 prepare/commit/rollback 无额外副作用）
  6. 边界：空批次、单项、首项失败、末项失败、重复名称、重复运行
"""

from __future__ import annotations

import unittest

from demo_participants import SimulatedParticipant
from two_phase_executor import (
    COMMIT_FAILED_PARTIAL,
    COMMIT_FAILED_ROLLED_BACK,
    EMPTY,
    OK,
    PREPARE_FAILED_ROLLED_BACK,
    PREPARE_FAILED_ROLLBACK_FAILED,
    ROLLED_BACK,
    SKIPPED,
    SUCCESS,
    UNRECOVERABLE,
    ItemResult,
    TwoPhaseBatchExecutor,
    assert_participant_idempotent,
)


def run(participants):
    return TwoPhaseBatchExecutor(participants).run()


class AllSuccessTests(unittest.TestCase):
    def test_three_participants_all_commit(self):
        parts = [SimulatedParticipant(f"svc-{i}") for i in range(3)]
        result = run(parts)

        self.assertEqual(result.status, SUCCESS)
        self.assertFalse(result.partial_success)
        self.assertEqual(result.unrecoverable, [])
        self.assertEqual([p.state for p in parts], ["committed"] * 3)
        self.assertEqual(
            [(r.prepare, r.commit, r.rollback) for r in result.items],
            [(OK, OK, "not_needed")] * 3,
        )
        self.assertIsNone(result.items[0].error)


class PrepareFailureTests(unittest.TestCase):
    def test_prepare_failure_aborts_and_rolls_back_in_reverse(self):
        a = SimulatedParticipant("a")
        b = SimulatedParticipant("b", fail_prepare=True)
        c = SimulatedParticipant("c")
        result = run([a, b, c])

        self.assertEqual(result.status, PREPARE_FAILED_ROLLED_BACK)
        self.assertFalse(result.partial_success)  # 干净回退，不算部分成功
        # a 已准备，必须被回退；b 准备失败；c 必须被跳过、完全未触碰
        self.assertEqual(a.state, "idle")
        self.assertEqual(result.items[0].rollback, ROLLED_BACK)
        self.assertEqual(result.items[1].prepare, "failed")
        self.assertEqual(result.items[2].prepare, SKIPPED)
        self.assertEqual(result.items[2].commit, SKIPPED)
        self.assertEqual(c.effect_counts["prepare"], 0)
        self.assertIn("prepare", result.items[1].error)

    def test_first_item_prepare_failure_nothing_to_rollback(self):
        result = run([
            SimulatedParticipant("a", fail_prepare=True),
            SimulatedParticipant("b"),
        ])
        self.assertEqual(result.status, PREPARE_FAILED_ROLLED_BACK)
        self.assertEqual(result.items[0].rollback, "not_needed")
        self.assertEqual(result.items[1].prepare, SKIPPED)

    def test_last_item_prepare_failure_all_previous_rolled_back(self):
        parts = [
            SimulatedParticipant("a"),
            SimulatedParticipant("b"),
            SimulatedParticipant("c", fail_prepare=True),
        ]
        result = run(parts)
        self.assertEqual(result.status, PREPARE_FAILED_ROLLED_BACK)
        self.assertEqual([p.state for p in parts], ["idle", "idle", "idle"])
        self.assertEqual(
            [r.rollback for r in result.items[:2]], [ROLLED_BACK, ROLLED_BACK]
        )


class CommitFailureTests(unittest.TestCase):
    def test_commit_failure_records_unrecoverable_and_marks_partial(self):
        a = SimulatedParticipant("a")                       # 已提交，不可逆
        b = SimulatedParticipant("b", fail_commit=True)    # 提交失败
        c = SimulatedParticipant("c")                       # 未提交，可回退
        result = run([a, b, c])

        self.assertEqual(result.status, COMMIT_FAILED_PARTIAL)
        self.assertTrue(result.partial_success)  # 部分成功必须显式标记
        # a 已提交且无法回退 -> unrecoverable
        self.assertEqual(result.items[0].rollback, UNRECOVERABLE)
        self.assertIn("a", result.unrecoverable)
        # b 提交失败但准备效果可回退
        self.assertEqual(result.items[1].commit, "failed")
        self.assertEqual(result.items[1].rollback, ROLLED_BACK)
        # c 跳过提交并回退
        self.assertEqual(result.items[2].commit, SKIPPED)
        self.assertEqual(result.items[2].rollback, ROLLED_BACK)
        self.assertIn("commit", result.items[1].error)

    def test_first_item_commit_failure_clean_rollback(self):
        # 没有任何已提交项：提交失败后整体可以干净回退
        a = SimulatedParticipant("a", fail_commit=True)
        b = SimulatedParticipant("b")
        result = run([a, b])
        self.assertEqual(result.status, COMMIT_FAILED_ROLLED_BACK)
        self.assertFalse(result.partial_success)
        self.assertEqual(result.unrecoverable, [])
        self.assertEqual(result.items[1].rollback, ROLLED_BACK)

    def test_rollback_after_commit_can_succeed(self):
        # 参与者允许提交后回退时，不应计入 unrecoverable
        a = SimulatedParticipant("a", committed_rollback_impossible=False)
        b = SimulatedParticipant("b", fail_commit=True)
        result = run([a, b])
        self.assertEqual(result.status, COMMIT_FAILED_ROLLED_BACK)
        self.assertEqual(result.items[0].rollback, ROLLED_BACK)
        self.assertEqual(result.unrecoverable, [])


class RollbackFailureTests(unittest.TestCase):
    def test_rollback_failure_during_prepare_abort(self):
        a = SimulatedParticipant("a", fail_rollback=True)
        b = SimulatedParticipant("b")
        c = SimulatedParticipant("c", fail_prepare=True)
        result = run([a, b, c])

        self.assertEqual(result.status, PREPARE_FAILED_ROLLBACK_FAILED)
        self.assertTrue(result.items[0].dirty)
        # 单项回退失败不阻断其他项继续回退
        self.assertEqual(result.items[1].rollback, ROLLED_BACK)
        self.assertIn("a", result.unrecoverable)
        self.assertIn("rollback", result.items[0].error)

    def test_rollback_failure_for_pending_after_commit_failure(self):
        a = SimulatedParticipant("a")  # 已提交不可逆
        b = SimulatedParticipant("b", fail_commit=True)
        c = SimulatedParticipant("c", fail_rollback=True)  # 未提交且回退失败
        result = run([a, b, c])
        self.assertEqual(result.status, COMMIT_FAILED_PARTIAL)
        self.assertEqual(result.items[2].rollback, "failed")
        self.assertIn("c", result.unrecoverable)


class IdempotencyTests(unittest.TestCase):
    def test_contract_assertion_passes_for_idempotent_participant(self):
        assert_participant_idempotent(lambda: SimulatedParticipant("x"))

    def test_contract_assertion_detects_non_idempotent_participant(self):
        class BadParticipant:
            name = "bad"
            state = "idle"
            effect_counts = {"prepare": 0, "commit": 0, "rollback": 0}

            def prepare(self):
                self.effect_counts["prepare"] += 1  # 不检查状态，重复计数

            def commit(self):
                self.effect_counts["commit"] += 1

            def rollback(self):
                self.effect_counts["rollback"] += 1

        with self.assertRaises(AssertionError):
            assert_participant_idempotent(BadParticipant)

    def test_simulated_participant_repeated_calls_no_extra_side_effects(self):
        p = SimulatedParticipant("p")
        p.prepare()
        p.prepare()
        self.assertEqual(p.effect_counts["prepare"], 1)
        p.commit()
        p.commit()
        p.commit()
        self.assertEqual(p.effect_counts["commit"], 1)

        q = SimulatedParticipant("q")
        q.prepare()
        q.rollback()
        q.rollback()
        self.assertEqual(q.effect_counts["rollback"], 1)
        self.assertEqual(q.state, "idle")

    def test_run_twice_does_not_reinvoke_successful_stages(self):
        # 执行器对已完成的运行重复执行：参与者侧的幂等保证无额外副作用
        parts = [SimulatedParticipant(f"svc-{i}") for i in range(2)]
        executor = TwoPhaseBatchExecutor(parts)
        first = executor.run()
        second = executor.run()
        self.assertEqual(first.status, SUCCESS)
        self.assertEqual(second.status, SUCCESS)
        for p in parts:
            self.assertEqual(p.effect_counts["prepare"], 1)
            self.assertEqual(p.effect_counts["commit"], 1)


class EdgeCaseTests(unittest.TestCase):
    def test_empty_batch_succeeds_vacuously(self):
        result = run([])
        self.assertEqual(result.status, EMPTY)
        self.assertEqual(result.items, [])
        self.assertFalse(result.partial_success)

    def test_single_participant_success(self):
        result = run([SimulatedParticipant("only")])
        self.assertEqual(result.status, SUCCESS)

    def test_single_participant_prepare_failure(self):
        result = run([SimulatedParticipant("only", fail_prepare=True)])
        self.assertEqual(result.status, PREPARE_FAILED_ROLLED_BACK)

    def test_duplicate_names_rejected_upfront(self):
        with self.assertRaises(ValueError):
            TwoPhaseBatchExecutor([
                SimulatedParticipant("dup"),
                SimulatedParticipant("dup"),
            ])

    def test_result_serialization_contains_per_item_data_and_conclusion(self):
        result = run([
            SimulatedParticipant("a"),
            SimulatedParticipant("b", fail_prepare=True),
        ])
        data = result.to_dict()
        self.assertIn("status", data)
        self.assertIn("partial_success", data)
        self.assertIn("unrecoverable", data)
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["items"][0]["name"], "a")
        # JSON 可序列化
        self.assertIn("PREPARE_FAILED", result.to_json())

    def test_item_result_flags(self):
        self.assertTrue(ItemResult("x", commit=OK).committed)
        self.assertFalse(ItemResult("x", commit=SKIPPED).committed)
        self.assertTrue(ItemResult("x", rollback=UNRECOVERABLE).dirty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
