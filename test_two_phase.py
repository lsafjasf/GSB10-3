"""two_phase 自测：全部成功 / 准备失败 / 提交失败 / 回退失败 / 幂等 / 边界。"""

import unittest

from two_phase import (
    FlakyParticipant, IdempotentParticipant, OverallStatus,
    TwoPhaseBatchExecutor,
)


class TestScenarios(unittest.TestCase):
    def test_all_success(self):
        ps = [FlakyParticipant("A"), FlakyParticipant("B"), FlakyParticipant("C")]
        r = TwoPhaseBatchExecutor().execute(ps)
        self.assertEqual(r.status, OverallStatus.SUCCESS)
        self.assertFalse(r.partial_success)
        self.assertTrue(all(i.prepare == "ok" and i.commit == "ok" for i in r.items))
        self.assertTrue(all(i.rollback == "not_needed" for i in r.items))
        for p in ps:  # 副作用恰好各一次
            self.assertEqual(p.effect_log, [f"prepare:{p.name}", f"commit:{p.name}"])

    def test_prepare_failure_aborts_and_rolls_back(self):
        a, b, c = (FlakyParticipant("A"), FlakyParticipant("B", fail_prepare=True),
                   FlakyParticipant("C"))
        r = TwoPhaseBatchExecutor().execute([a, b, c])
        self.assertEqual(r.status, OverallStatus.ABORTED)
        self.assertFalse(r.partial_success)
        ia, ib, ic = r.items
        self.assertEqual((ia.prepare, ia.rollback), ("ok", "ok"))
        self.assertEqual(ib.prepare, "failed")
        self.assertEqual(ic.prepare, "skipped")  # 立刻中止，C 未被触碰
        self.assertEqual(c.effect_log, [])
        self.assertEqual(a.effect_log, ["prepare:A", "rollback:A"])  # 逆序回退
        self.assertNotIn("commit:A", a.effect_log)

    def test_rollback_order_is_reverse(self):
        order = []

        class Rec(IdempotentParticipant):
            def _do_rollback(self):
                order.append(self.name)
                super()._do_rollback()

        ps = [Rec("A"), Rec("B"), Rec("C"), FlakyParticipant("D", fail_prepare=True)]
        TwoPhaseBatchExecutor().execute(ps)
        self.assertEqual(order, ["C", "B", "A"])

    def test_commit_failure_marks_unrecoverable_partial(self):
        a, b, c = (FlakyParticipant("A"), FlakyParticipant("B"),
                   FlakyParticipant("C", fail_commit=True))
        r = TwoPhaseBatchExecutor().execute([a, b, c])
        self.assertEqual(r.status, OverallStatus.PARTIAL_COMMIT)
        self.assertTrue(r.partial_success)  # 部分成功显式标记
        ia, ib, ic = r.items
        self.assertTrue(ia.unrecoverable and ib.unrecoverable)
        self.assertEqual((ia.rollback, ib.rollback), ("impossible", "impossible"))
        self.assertEqual(ic.commit, "failed")
        self.assertFalse(ic.unrecoverable)

    def test_rollback_failure_is_recorded(self):
        a = FlakyParticipant("A", fail_rollback=True)
        b = FlakyParticipant("B", fail_prepare=True)
        r = TwoPhaseBatchExecutor().execute([a, b])
        self.assertEqual(r.status, OverallStatus.ABORTED_ROLLBACK_FAILED)
        self.assertEqual(r.items[0].rollback, "failed")
        self.assertIn("rollback_error", r.items[0].detail)

    def test_commit_failure_with_remaining_rollback_failure(self):
        # 提交失败时，未提交项回退也失败 -> 仍 PARTIAL_COMMIT 且记录回退失败
        a = FlakyParticipant("A")
        b = FlakyParticipant("B", fail_rollback=True)
        c = FlakyParticipant("C", fail_commit=True)
        # 让 B 在 C 之后提交不到：顺序 A, C(fail), B -> B 是已准备未提交，需回退且失败
        r = TwoPhaseBatchExecutor().execute([a, c, b])
        self.assertEqual(r.status, OverallStatus.PARTIAL_COMMIT)
        self.assertTrue(r.partial_success)
        ib = r.items[2]
        self.assertEqual(ib.rollback, "failed")
        self.assertTrue(r.items[0].unrecoverable)


class TestIdempotency(unittest.TestCase):
    def test_participant_idempotent_assertion(self):
        for p in [FlakyParticipant("X"), FlakyParticipant("Y")]:
            p.prepare()
            p.assert_idempotent()  # 内部重复调用 prepare/commit/rollback
            self.assertEqual(p.effect_log, [f"prepare:{p.name}", f"commit:{p.name}"])

    def test_repeat_prepare_commit_no_side_effect(self):
        p = FlakyParticipant("A")
        p.prepare(); p.prepare(); p.prepare()
        self.assertEqual(p.effect_log, ["prepare:A"])
        p.commit(); p.commit()
        self.assertEqual(p.effect_log, ["prepare:A", "commit:A"])

    def test_rollback_idempotent(self):
        p = FlakyParticipant("A")
        p.prepare()
        p.rollback(); p.rollback(); p.rollback()
        self.assertEqual(p.effect_log, ["prepare:A", "rollback:A"])

    def test_commit_without_prepare_rejected(self):
        p = FlakyParticipant("A")
        with self.assertRaises(Exception):
            p.commit()

    def test_executor_rerun_is_noop(self):
        # 幂等断言（执行器级）：同一批参与者重复 execute 不产生新副作用
        ps = [FlakyParticipant("A"), FlakyParticipant("B")]
        ex = TwoPhaseBatchExecutor()
        r1 = ex.execute(ps)
        logs = [list(p.effect_log) for p in ps]
        r2 = ex.execute(ps)
        self.assertEqual(r1.status, r2.status)
        self.assertEqual([list(p.effect_log) for p in ps], logs)


class TestEdgeCases(unittest.TestCase):
    def test_empty_batch(self):
        r = TwoPhaseBatchExecutor().execute([])
        self.assertEqual(r.status, OverallStatus.SUCCESS)
        self.assertEqual(r.items, [])

    def test_single_participant(self):
        r = TwoPhaseBatchExecutor().execute([FlakyParticipant("only")])
        self.assertEqual(r.status, OverallStatus.SUCCESS)
        self.assertEqual(len(r.items), 1)

    def test_first_item_prepare_fails_nothing_to_rollback(self):
        r = TwoPhaseBatchExecutor().execute(
            [FlakyParticipant("A", fail_prepare=True), FlakyParticipant("B")])
        self.assertEqual(r.status, OverallStatus.ABORTED)
        self.assertTrue(all(i.rollback == "not_needed" for i in r.items))

    def test_last_item_commit_fails_all_others_unrecoverable(self):
        ps = [FlakyParticipant("A"), FlakyParticipant("B"),
              FlakyParticipant("C", fail_commit=True)]
        r = TwoPhaseBatchExecutor().execute(ps)
        self.assertEqual(sum(1 for i in r.items if i.unrecoverable), 2)

    def test_duplicate_participant_deduped(self):
        p = FlakyParticipant("A")
        r = TwoPhaseBatchExecutor().execute([p, p, p])
        self.assertEqual(len(r.items), 1)
        self.assertEqual(p.effect_log, ["prepare:A", "commit:A"])

    def test_result_json_serializable(self):
        r = TwoPhaseBatchExecutor().execute([FlakyParticipant("A")])
        self.assertIn('"SUCCESS"', r.to_json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
