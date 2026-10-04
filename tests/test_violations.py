"""约定的「被破坏用例」：每个违规输入/违规调用必须被对应断言拒绝。"""

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


def make_processor(events=()):
    sink = RecordingSink()
    processor = BatchProcessor(sink, "b")
    for event in events:
        processor.add_event(event)
    return processor, sink


class ViolationTests(unittest.TestCase):
    def assert_violation(self, convention_id, func):
        with self.assertRaises(ContractViolation) as ctx:
            func()
        self.assertIn(f"[{convention_id}]", str(ctx.exception))

    # ---- 数据结构形状 ----

    def test_S1_missing_key_rejected(self):
        event = new_event("e", "trade", "AAPL", 1, 0)
        del event["trace_id"]
        self.assert_violation("S1", lambda: make_processor([event]))

    def test_S1_extra_key_rejected(self):
        event = new_event("e", "trade", "AAPL", 1, 0)
        event["bonus"] = 1
        self.assert_violation("S1", lambda: make_processor([event]))

    def test_S1_none_value_rejected(self):
        event = new_event("e", "trade", "AAPL", 1, 0)
        event["source"] = None
        self.assert_violation("S1", lambda: make_processor([event]))

    def test_S2_trade_without_symbol_rejected(self):
        self.assert_violation("S2", lambda: make_processor(
            [new_event("e", "trade", None, 1, 0)]))

    def test_S2_quote_with_symbol_rejected(self):
        self.assert_violation("S2", lambda: make_processor(
            [new_event("e", "quote", "AAPL", 1, 0)]))

    def test_S3_tampered_receipt_shape_rejected(self):
        # 模拟有人把 receipt 的 committed_at 改成了 str：S3 必须拦下。
        sink = RecordingSink()
        processor = BatchProcessor(sink, "b")
        processor.add_event(new_event("e", "quote", None, 1, 0))
        processor.close()
        real_snapshot = processor._snapshot_locked

        def bad_snapshot():
            receipt = real_snapshot()
            receipt["committed_at"] = "now"
            return receipt

        processor._snapshot_locked = bad_snapshot
        self.assert_violation("S3", processor.commit)

    def test_S3_tampered_seq_type_rejected(self):
        sink = RecordingSink()
        processor = BatchProcessor(sink, "b")
        processor.add_event(new_event("e", "quote", None, 1, 0))
        processor.close()
        real_snapshot = processor._snapshot_locked

        def bad_snapshot():
            receipt = real_snapshot()
            receipt["seq"] = "3"
            return receipt

        processor._snapshot_locked = bad_snapshot
        self.assert_violation("S3", processor.commit)

    # ---- 调用顺序 ----

    def test_O1_commit_before_close_rejected(self):
        processor, _ = make_processor([new_event("e", "quote", None, 1, 0)])
        self.assert_violation("O1", processor.commit)

    def test_O1_add_after_close_rejected(self):
        processor, _ = make_processor([new_event("e", "quote", None, 1, 0)])
        processor.close()
        self.assert_violation(
            "O1", lambda: processor.add_event(new_event("f", "quote", None, 1, 1)))

    def test_O1_double_commit_rejected(self):
        processor, _ = make_processor([new_event("e", "quote", None, 1, 0)])
        processor.close()
        processor.commit()
        self.assert_violation("O1", processor.commit)

    def test_O2_out_of_order_timestamp_rejected(self):
        processor, _ = make_processor([new_event("a", "quote", None, 1, 10)])
        self.assert_violation(
            "O2", lambda: processor.add_event(new_event("b", "quote", None, 1, 9)))

    def test_O3_register_callback_after_commit_rejected(self):
        processor, _ = make_processor([new_event("e", "quote", None, 1, 0)])
        processor.close()
        processor.commit()
        self.assert_violation("O3", lambda: processor.set_on_commit(lambda r: None))

    # ---- 取值范围 ----

    def test_R1_empty_event_id_rejected(self):
        self.assert_violation("R1", lambda: make_processor(
            [new_event("", "quote", None, 1, 0)]))

    def test_R1_non_string_event_id_rejected(self):
        self.assert_violation("R1", lambda: make_processor(
            [new_event(123, "quote", None, 1, 0)]))

    def test_R2_unknown_type_rejected(self):
        self.assert_violation("R2", lambda: make_processor(
            [new_event("e", "transfer", None, 1, 0)]))

    def test_R3_float_amount_rejected(self):
        self.assert_violation("R3", lambda: make_processor(
            [new_event("e", "quote", None, 1.5, 0)]))

    def test_R3_bool_amount_rejected(self):
        self.assert_violation("R3", lambda: make_processor(
            [new_event("e", "quote", None, True, 0)]))

    def test_R3_amount_above_max_rejected(self):
        self.assert_violation("R3", lambda: make_processor(
            [new_event("e", "quote", None, 1_000_000_001, 0)]))

    def test_R4_negative_occurred_at_rejected(self):
        self.assert_violation("R4", lambda: make_processor(
            [new_event("e", "quote", None, 1, -1)]))

    def test_R4_float_occurred_at_rejected(self):
        self.assert_violation("R4", lambda: make_processor(
            [new_event("e", "quote", None, 1, 1.0)]))

    def test_R5_list_tags_rejected(self):
        self.assert_violation("R5", lambda: make_processor(
            [new_event("e", "quote", None, 1, 0, tags=["a"])]))

    def test_R5_non_string_tag_rejected(self):
        self.assert_violation("R5", lambda: make_processor(
            [new_event("e", "quote", None, 1, 0, tags=(1,))]))

    def test_R5_duplicate_tags_rejected(self):
        self.assert_violation("R5", lambda: make_processor(
            [new_event("e", "quote", None, 1, 0, tags=("a", "a"))]))

    def test_R5_too_many_tags_rejected(self):
        self.assert_violation("R5", lambda: make_processor(
            [new_event("e", "quote", None, 1, 0, tags=("a", "b", "c", "d"))]))

    def test_R6_symbol_outside_whitelist_rejected(self):
        self.assert_violation("R6", lambda: make_processor(
            [new_event("e", "trade", "MSFT", 1, 0)]))

    def test_R7_batch_size_limit_rejected(self):
        processor, _ = make_processor()
        for index in range(3):
            processor.add_event(new_event(f"e{index}", "quote", None, 1, index))
        processor._max_batch = 3
        self.assert_violation(
            "R7", lambda: processor.add_event(new_event("e3", "quote", None, 1, 3)))

    # ---- 线程约束 ----

    def test_T1_call_from_foreign_thread_rejected(self):
        processor, _ = make_processor()
        errors = []
        done = threading.Event()

        def foreign():
            try:
                processor.add_event(new_event("e", "quote", None, 1, 0))
            except ContractViolation as exc:
                errors.append(str(exc))
            finally:
                done.set()

        threading.Thread(target=foreign).start()
        self.assertTrue(done.wait(5))
        self.assertEqual(len(errors), 1)
        self.assertIn("[T1]", errors[0])

    def test_T2_publish_must_not_hold_lock(self):
        # 模拟有人把 sink 调用挪进了锁内：T2 断言必须拦截。
        processor, _ = make_processor([new_event("e", "quote", None, 1, 0)])
        processor.close()
        with processor._lock:
            self.assert_violation(
                "T2", lambda: processor._publish(processor._snapshot_locked()))

    def test_T3_locked_method_without_lock_rejected(self):
        processor, _ = make_processor()
        self.assert_violation("T3", processor._snapshot_locked)
        self.assert_violation("T3", processor._advance_locked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
