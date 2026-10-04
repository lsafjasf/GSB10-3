"""边界用例：每条取值范围/顺序约定的临界值两侧。"""

import unittest

from src.contracts import ContractViolation
from src.events import new_event
from src.processor import BatchProcessor, MAX_BATCH


class RecordingSink:
    def publish(self, receipt):
        pass


def accept(event):
    processor = BatchProcessor(RecordingSink(), "b")
    processor.add_event(event)


class BoundaryTests(unittest.TestCase):
    # R3 amount ∈ [0, 1_000_000_000]
    def test_R3_amount_zero_ok(self):
        accept(new_event("e", "quote", None, 0, 0))

    def test_R3_amount_max_ok(self):
        accept(new_event("e", "quote", None, 1_000_000_000, 0))

    def test_R3_amount_minus_one_rejected(self):
        with self.assertRaises(ContractViolation):
            accept(new_event("e", "quote", None, -1, 0))

    def test_R3_amount_max_plus_one_rejected(self):
        with self.assertRaises(ContractViolation):
            accept(new_event("e", "quote", None, 1_000_000_001, 0))

    # R4 occurred_at >= 0
    def test_R4_occurred_at_zero_ok(self):
        accept(new_event("e", "quote", None, 0, 0))

    def test_R4_occurred_at_minus_one_rejected(self):
        with self.assertRaises(ContractViolation):
            accept(new_event("e", "quote", None, 0, -1))

    # R5 tags: 0..3 个、不重复
    def test_R5_zero_tags_ok(self):
        accept(new_event("e", "quote", None, 0, 0, tags=()))

    def test_R5_three_tags_ok(self):
        accept(new_event("e", "quote", None, 0, 0, tags=("a", "b", "c")))

    def test_R5_four_tags_rejected(self):
        with self.assertRaises(ContractViolation):
            accept(new_event("e", "quote", None, 0, 0, tags=("a", "b", "c", "d")))

    # R7 批次上限：第 MAX_BATCH 个成功，第 MAX_BATCH+1 个失败
    def test_R7_exactly_max_events_ok(self):
        processor = BatchProcessor(RecordingSink(), "b")
        for index in range(MAX_BATCH):
            processor.add_event(new_event(f"e{index}", "quote", None, 0, index))
        self.assertEqual(len(processor._events), MAX_BATCH)

    def test_R7_max_plus_one_rejected(self):
        processor = BatchProcessor(RecordingSink(), "b")
        for index in range(MAX_BATCH):
            processor.add_event(new_event(f"e{index}", "quote", None, 0, index))
        with self.assertRaises(ContractViolation):
            processor.add_event(new_event("overflow", "quote", None, 0, MAX_BATCH))

    # O2 时间戳非递减：相等合法，小 1 非法
    def test_O2_equal_timestamp_ok(self):
        processor = BatchProcessor(RecordingSink(), "b")
        processor.add_event(new_event("a", "quote", None, 0, 5))
        processor.add_event(new_event("b", "quote", None, 0, 5))

    def test_O2_one_less_rejected(self):
        processor = BatchProcessor(RecordingSink(), "b")
        processor.add_event(new_event("a", "quote", None, 0, 5))
        with self.assertRaises(ContractViolation):
            processor.add_event(new_event("b", "quote", None, 0, 4))

    # R1 event_id：单字符合法，空串非法
    def test_R1_single_char_id_ok(self):
        accept(new_event("x", "quote", None, 0, 0))

    def test_R1_empty_id_rejected(self):
        with self.assertRaises(ContractViolation):
            accept(new_event("", "quote", None, 0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
