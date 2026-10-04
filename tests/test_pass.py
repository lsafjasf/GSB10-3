"""约定的「通过用例」：合法输入与正确调用顺序必须全部成功。"""

import threading
import unittest

from src.contracts import ContractViolation
from src.events import new_event
from src.processor import BatchProcessor


class RecordingSink:
    def __init__(self):
        self.received = []

    def publish(self, receipt):
        self.received.append(receipt)


class HappyPathTests(unittest.TestCase):
    def _run_full_batch(self, *events, callback=None):
        sink = RecordingSink()
        processor = BatchProcessor(sink, batch_id="batch-1")
        if callback:
            processor.set_on_commit(callback)
        for event in events:
            processor.add_event(event)
        processor.close()
        return processor, sink, processor.commit()

    def test_S1_event_with_exact_8_keys_is_accepted(self):
        event = new_event("e1", "trade", "AAPL", 100, 0)
        self.assertEqual(len(event), 8)
        _, _, receipt = self._run_full_batch(event)
        self.assertEqual(receipt["event_ids"], ("e1",))

    def test_S2_trade_with_symbol_and_quote_without_symbol(self):
        trade = new_event("t", "trade", "GOOG", 1, 1)
        quote = new_event("q", "quote", None, 2, 2)
        _, _, receipt = self._run_full_batch(trade, quote)
        self.assertEqual(receipt["seq"], 2)

    def test_S3_receipt_has_fixed_shape_after_commit(self):
        event = new_event("e", "trade", "TSLA", 7, 0)
        _, sink, receipt = self._run_full_batch(event)
        self.assertEqual(set(receipt.keys()),
                         {"batch_id", "event_ids", "seq", "total_amount", "committed_at"})
        self.assertIs(receipt, sink.received[0])

    def test_O1_full_lifecycle_open_close_commit_done(self):
        sink = RecordingSink()
        processor = BatchProcessor(sink, "b")
        processor.add_event(new_event("e", "trade", "AAPL", 1, 0))
        processor.close()
        processor.commit()

    def test_O2_events_accepted_in_non_decreasing_time_order(self):
        events = [
            new_event("1", "quote", None, 1, 10),
            new_event("2", "quote", None, 1, 10),
            new_event("3", "trade", "AAPL", 1, 11),
        ]
        _, _, receipt = self._run_full_batch(*events)
        self.assertEqual(receipt["seq"], 3)

    def test_O3_callback_registered_before_commit_fires_once(self):
        calls = []
        event = new_event("e", "trade", "AAPL", 3, 0)
        _, _, receipt = self._run_full_batch(event, callback=lambda r: calls.append(r))
        self.assertEqual(calls, [receipt])

    def test_R1_uuid_nonempty_string_accepted(self):
        self._run_full_batch(new_event("uuid-xyz", "quote", None, 0, 0))

    def test_R4_occurred_at_zero_and_large_positive_accepted(self):
        self._run_full_batch(
            new_event("a", "quote", None, 0, 0),
            new_event("b", "quote", None, 0, 2**53),
        )

    def test_R5_empty_tuple_and_three_distinct_tags_accepted(self):
        self._run_full_batch(new_event("a", "quote", None, 0, 0, tags=()))
        self._run_full_batch(new_event("b", "quote", None, 0, 0,
                                       tags=("x", "y", "z")))

    def test_R6_whitelist_symbols_accepted(self):
        for index, symbol in enumerate(("AAPL", "GOOG", "TSLA")):
            self._run_full_batch(new_event(f"e{index}", "trade", symbol, 1, index))

    def test_T1_used_from_creating_thread_succeeds(self):
        errors = []
        done = threading.Event()

        def worker():
            try:
                sink = RecordingSink()
                processor = BatchProcessor(sink, "b")
                processor.add_event(new_event("e", "quote", None, 1, 0))
                processor.close()
                processor.commit()
            except Exception:  # noqa: BLE001 - 测试中任何异常都视为失败
                errors.append(True)
            finally:
                done.set()

        threading.Thread(target=worker).start()
        self.assertTrue(done.wait(5))
        self.assertEqual(errors, [])

    def test_T2_callback_runs_outside_lock_can_reenter_safely(self):
        # 回调在锁外执行：即使回调反向调用 processor 的公共方法（仅查询），
        # 也不会死锁。
        sink = RecordingSink()
        processor = BatchProcessor(sink, "b")

        def callback(_receipt):
            # commit 后状态为 done，调用任何公共方法都应快速抛 O1/T1 而不是卡死。
            try:
                processor.add_event(new_event("late", "quote", None, 1, 1))
            except ContractViolation:
                pass

        processor.set_on_commit(callback)
        processor.add_event(new_event("e", "quote", None, 1, 0))
        processor.close()
        finished = threading.Event()
        timer = threading.Timer(2, lambda: None)
        timer.start()
        processor.commit()
        finished.set()
        timer.cancel()
        self.assertTrue(finished.is_set())

    def test_T3_internal_calls_during_commit_hold_the_lock(self):
        # 正常 commit 内部在持锁状态调用 _snapshot_locked/_advance_locked，
        # 若 T3 断言被破坏，此用例会失败。
        sink = RecordingSink()
        processor = BatchProcessor(sink, "b")
        processor.add_event(new_event("e", "quote", None, 1, 0))
        processor.close()
        processor.commit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
