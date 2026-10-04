"""bulkhead_pool 的单元测试（仅标准库，python3 -m unittest 直接运行）。

覆盖：
- 正常并发（两类并行打满各自保证舱位，互不拒绝）
- 单类别打满（并发上限 + 队列上限 + 拒绝计数 + 隔离性）
- 借用后归还（借用发生、收回时序、归还后可再借）
- 任务抛异常（舱位不泄漏、failed 计数、后续任务正常）
- 边界用例（max_queue=0、非法配置、未知类别、shutdown 行为、DISCARD 策略）
"""

import threading
import time
import unittest
from concurrent.futures import CancelledError

from bulkhead_pool import (
    ABORT,
    DISCARD,
    BulkheadThreadPool,
    CategoryConfig,
    PoolShutdownError,
    RejectedExecutionError,
)


class Gate:
    """可控闸门任务：进入时记录时间并通知，随后阻塞直到 release()。"""

    def __init__(self):
        self.entered = threading.Event()
        self._release = threading.Event()
        self.entered_at = None

    def __call__(self):
        self.entered_at = time.monotonic()
        self.entered.set()
        if not self._release.wait(timeout=10):
            raise RuntimeError("gate timeout")
        return self.entered_at

    def release(self):
        self._release.set()

    def wait_entered(self, timeout=5.0):
        if not self.entered.wait(timeout):
            raise AssertionError("task did not start in time")


def make_pool(**overrides):
    """默认两类别，各 2 个保证舱位、队列上限 2。"""
    configs = {
        "A": CategoryConfig(max_concurrency=2, max_queue=2),
        "B": CategoryConfig(max_concurrency=2, max_queue=2),
    }
    configs.update(overrides)
    return BulkheadThreadPool(configs)


class TestNormalConcurrency(unittest.TestCase):
    def test_two_categories_run_at_full_guaranteed_capacity(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        gates = [Gate() for _ in range(4)]
        futs = [pool.submit("A" if i < 2 else "B", gates[i]) for i in range(4)]
        for g in gates:
            g.wait_entered()
        stats = pool.all_stats()
        self.assertEqual(stats["A"].active, 2)
        self.assertEqual(stats["B"].active, 2)
        self.assertEqual(pool.total_available_slots(), 0)
        for g in gates:
            g.release()
        for f in futs:
            f.result(timeout=5)
        stats = pool.all_stats()
        self.assertEqual(stats["A"].rejected, 0)
        self.assertEqual(stats["B"].rejected, 0)
        self.assertEqual(stats["A"].completed, 2)
        self.assertEqual(stats["B"].completed, 2)
        self.assertEqual(pool.total_available_slots(), 4)

    def test_results_and_kwargs(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        fut = pool.submit("A", lambda x, scale=1: x * scale, 21, scale=2)
        self.assertEqual(fut.result(timeout=5), 42)


class TestSaturationAndIsolation(unittest.TestCase):
    def test_slow_category_saturates_itself_but_not_others(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        # B 先占满自己的舱位（另一路正常业务），全局不再有可借用的空闲舱位。
        b_gates = [Gate() for _ in range(2)]
        b_running = [pool.submit("B", g) for g in b_gates]
        for g in b_gates:
            g.wait_entered()

        # 慢业务 A：2 运行 + 2 排队打满自己的配额，第 5 个任务被拒绝并计数。
        a_gates = [Gate() for _ in range(2)]
        a_running = [pool.submit("A", g) for g in a_gates]
        for g in a_gates:
            g.wait_entered()
        a_queued = [pool.submit("A", lambda: "q") for _ in range(2)]
        with self.assertRaises(RejectedExecutionError) as ctx:
            pool.submit("A", lambda: "overflow")
        self.assertEqual(ctx.exception.category, "A")
        astats = pool.stats("A")
        self.assertEqual(astats.rejected, 1)
        self.assertEqual(astats.queued, 2)
        self.assertEqual(astats.active, 2)

        # 隔离性：A 打满不影响 B —— B 正常执行，且还能排队到自己的上限。
        b_queued = [pool.submit("B", lambda: "bq") for _ in range(2)]
        bstats = pool.stats("B")
        self.assertEqual(bstats.active, 2)
        self.assertEqual(bstats.queued, 2)
        self.assertEqual(bstats.rejected, 0)

        for g in a_gates + b_gates:
            g.release()
        for f in a_running + a_queued + b_running + b_queued:
            f.result(timeout=5)
        stats = pool.all_stats()
        self.assertEqual(stats["A"].completed, 4)
        self.assertEqual(stats["A"].rejected, 1)
        self.assertEqual(stats["B"].completed, 4)
        self.assertEqual(stats["B"].rejected, 0)
        self.assertEqual(pool.total_available_slots(), 4)

    def test_queue_limit_is_per_category(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        # A、B 各占满自己的舱位，全局无空闲舱位。
        a_gates = [Gate() for _ in range(2)]
        b_gates = [Gate() for _ in range(2)]
        for g in a_gates:
            pool.submit("A", g)
        for g in b_gates:
            pool.submit("B", g)
        for g in a_gates + b_gates:
            g.wait_entered()
        # B 排队到上限后，下一个被拒绝，拒绝计数只算在 B 头上。
        for _ in range(2):
            pool.submit("B", lambda: None)
        with self.assertRaises(RejectedExecutionError):
            pool.submit("B", lambda: None)
        self.assertEqual(pool.stats("B").rejected, 1)
        # A 的队列仍然可用（排队等待收回自己的舱位）。
        pool.submit("A", lambda: None)
        astats = pool.stats("A")
        self.assertEqual(astats.queued, 1)
        self.assertEqual(astats.rejected, 0)
        for g in a_gates + b_gates:
            g.release()


class TestBorrowAndReclaim(unittest.TestCase):
    def test_borrow_reclaim_and_return_timeline(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)

        # t0: B 打满自己的 2 个保证舱位。
        b1, b2 = Gate(), Gate()
        pool.submit("B", b1)
        pool.submit("B", b2)
        b1.wait_entered()
        b2.wait_entered()

        # t1: A 空闲，B 借到 A 的 2 个舱位（borrowed=2）。
        b3, b4 = Gate(), Gate()
        pool.submit("B", b3)
        pool.submit("B", b4)
        b3.wait_entered()
        b4.wait_entered()
        bstats = pool.stats("B")
        self.assertEqual(bstats.active, 4)
        self.assertEqual(bstats.borrowed, 2)
        self.assertEqual(pool.total_available_slots(), 0)

        # t2: 原属类别 A 出现需求，但舱位被借满，只能排队等待收回。
        aq1, aq2 = Gate(), Gate()
        f_aq1 = pool.submit("A", aq1)
        f_aq2 = pool.submit("A", aq2)
        self.assertEqual(pool.stats("A").queued, 2)

        # t3: b2 结束 -> 舱位立即收回给 A（aq1 先于 B 的新借用者运行）。
        b2.release()
        aq1.wait_entered()
        self.assertFalse(aq2.entered.is_set())

        # t4: B 提交新任务，只能排在 A 之后（收回优先于借用）。
        b5 = Gate()
        f_b5 = pool.submit("B", b5)
        time.sleep(0.1)  # 给调度留出时间，确认 b5 不会插队
        self.assertFalse(b5.entered.is_set())

        # t5: b3 结束 -> 收回给 aq2，b5 仍等待。
        b3.release()
        aq2.wait_entered()
        self.assertFalse(b5.entered.is_set())

        # t6: aq1 结束 -> 空闲舱位按序借给 b5。
        aq1.release()
        b5.wait_entered()
        self.assertGreaterEqual(b5.entered_at, aq1.entered_at)

        # 时序断言：收回发生在借用者结束之后。
        self.assertGreater(aq1.entered_at, b2.entered_at)
        self.assertGreater(aq2.entered_at, b3.entered_at)

        # t7: 全部释放，舱位归还；A 可用舱位恢复为 2 保证 + 2 可借，无泄漏。
        for g in (b1, b4, aq2, b5):
            g.release()
        for f in (f_aq1, f_aq2, f_b5):
            f.result(timeout=5)
        self.assertEqual(pool.total_available_slots(), 4)
        self.assertEqual(pool.available_slots("A"), 4)
        self.assertEqual(pool.stats("B").rejected, 0)

    def test_borrowed_slots_return_after_borrower_finishes(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        gates = [Gate() for _ in range(4)]
        for g in gates:
            pool.submit("B", g)
        for g in gates:
            g.wait_entered()
        self.assertEqual(pool.stats("B").borrowed, 2)
        for g in gates:
            g.release()
        deadline = time.monotonic() + 5
        while pool.total_available_slots() < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(pool.total_available_slots(), 4)
        # 归还后 A 立即可用自己的舱位。
        a_gate = Gate()
        pool.submit("A", a_gate)
        a_gate.wait_entered()
        a_gate.release()


class TestExceptionSafety(unittest.TestCase):
    def test_exception_does_not_leak_slots(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)

        def boom():
            raise ValueError("boom")

        futs = [pool.submit("A", boom) for _ in range(2)]
        for f in futs:
            with self.assertRaises(ValueError):
                f.result(timeout=5)
        stats = pool.stats("A")
        self.assertEqual(stats.failed, 2)
        self.assertEqual(stats.active, 0)
        # 关键断言：异常之后舱位全部可用，没有泄漏（2 保证 + 2 可借）。
        self.assertEqual(pool.available_slots("A"), 4)
        self.assertEqual(pool.total_available_slots(), 4)

        # 后续任务照常执行。
        ok = [pool.submit("A", lambda i=i: i * 2) for i in range(2)]
        self.assertEqual([f.result(timeout=5) for f in ok], [0, 2])
        self.assertEqual(pool.stats("A").completed, 2)

    def test_exception_in_borrowed_slot_returns_it_to_owner(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        gates = [Gate() for _ in range(2)]
        for g in gates:
            pool.submit("B", g)
        for g in gates:
            g.wait_entered()
        # B 借用 A 的舱位，借来的任务抛异常。
        borrowed = [pool.submit("B", lambda: 1 / 0) for _ in range(2)]
        for f in borrowed:
            with self.assertRaises(ZeroDivisionError):
                f.result(timeout=5)
        self.assertEqual(pool.stats("B").failed, 2)
        # 借来的舱位已归还，A 可立即使用。
        self.assertEqual(pool.available_slots("A"), 2)
        a_gate = Gate()
        pool.submit("A", a_gate)
        a_gate.wait_entered()
        a_gate.release()
        for g in gates:
            g.release()


class TestEdgeCases(unittest.TestCase):
    def test_zero_queue_capacity_rejects_when_busy(self):
        pool = BulkheadThreadPool(
            {"A": CategoryConfig(max_concurrency=1, max_queue=0)}
        )
        self.addCleanup(pool.shutdown)
        gate = Gate()
        pool.submit("A", gate)
        gate.wait_entered()
        with self.assertRaises(RejectedExecutionError):
            pool.submit("A", lambda: None)
        self.assertEqual(pool.stats("A").rejected, 1)
        gate.release()

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            BulkheadThreadPool({})
        with self.assertRaises(ValueError):
            BulkheadThreadPool(
                {"A": CategoryConfig(max_concurrency=0, max_queue=1)}
            )
        with self.assertRaises(ValueError):
            BulkheadThreadPool(
                {"A": CategoryConfig(max_concurrency=1, max_queue=-1)}
            )

    def test_unknown_category(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        with self.assertRaises(KeyError):
            pool.submit("nope", lambda: None)

    def test_discard_policy_counts_and_cancels(self):
        pool = BulkheadThreadPool(
            {"A": CategoryConfig(max_concurrency=1, max_queue=1, policy=DISCARD)}
        )
        self.addCleanup(pool.shutdown)
        gate = Gate()
        pool.submit("A", gate)
        gate.wait_entered()
        pool.submit("A", lambda: None)  # 占满队列
        dropped = pool.submit("A", lambda: None)  # 不抛异常，静默丢弃
        self.assertTrue(dropped.cancelled())
        with self.assertRaises(CancelledError):
            dropped.result(timeout=5)
        self.assertEqual(pool.stats("A").rejected, 1)
        gate.release()

    def test_default_policy_is_abort(self):
        pool = make_pool()
        self.addCleanup(pool.shutdown)
        self.assertEqual(ABORT.name, "abort")
        # B 占满自己的舱位，使全局没有可借用的空闲舱位。
        b_gates = [Gate() for _ in range(2)]
        for g in b_gates:
            pool.submit("B", g)
        for g in b_gates:
            g.wait_entered()
        gate1, gate2 = Gate(), Gate()
        pool.submit("A", gate1)
        pool.submit("A", gate2)
        gate1.wait_entered()
        gate2.wait_entered()
        pool.submit("A", lambda: None)
        pool.submit("A", lambda: None)
        with self.assertRaises(RejectedExecutionError):
            pool.submit("A", lambda: None)
        gate1.release()
        gate2.release()
        for g in b_gates:
            g.release()

    def test_shutdown_rejects_new_submissions(self):
        pool = make_pool()
        pool.shutdown()
        with self.assertRaises(PoolShutdownError):
            pool.submit("A", lambda: None)

    def test_shutdown_cancel_pending(self):
        pool = make_pool()
        gate1, gate2 = Gate(), Gate()
        pool.submit("A", gate1)
        pool.submit("A", gate2)
        gate1.wait_entered()
        gate2.wait_entered()
        pending = [pool.submit("A", lambda: "never") for _ in range(2)]
        # 闸门未释放时取消排队任务，避免与 worker 消费产生竞态。
        pool.shutdown(wait=False, cancel_pending=True)
        for f in pending:
            self.assertTrue(f.cancelled())
        gate1.release()
        gate2.release()
        pool.shutdown()  # 等运行中任务结束、worker 退出
        with self.assertRaises(PoolShutdownError):
            pool.submit("A", lambda: None)

    def test_context_manager(self):
        with make_pool() as pool:
            fut = pool.submit("A", lambda: "ok")
            self.assertEqual(fut.result(timeout=5), "ok")
        with self.assertRaises(PoolShutdownError):
            pool.submit("A", lambda: None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
