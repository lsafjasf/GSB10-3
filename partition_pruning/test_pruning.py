"""分区裁剪与下推的自测（unittest，仅标准库）。

运行：python3 -m unittest test_pruning -v
"""

import unittest

from pruning import And, Eq, In, Interval, Or, Partition, PartitionedTable, Range


def make_table(rows_per_partition=5):
    """month 1..12 每月一个分区。"""
    parts = []
    for m in range(1, 13):
        rows = [
            {"id": m * 1000 + i, "month": m, "amount": (m * 3 + i * 7) % 50}
            for i in range(rows_per_partition)
        ]
        parts.append(Partition(f"p{m:02d}", m, m, rows))
    return PartitionedTable("t", "month", parts)


def assert_consistent(testcase, table, cond):
    """裁剪+下推 的结果必须与 全扫描基线 一致。"""
    base_rows, base_stats = table.query(cond, prune=False, pushdown=False)
    opt_rows, opt_stats = table.query(cond, prune=True, pushdown=True)
    testcase.assertEqual(
        sorted(r["id"] for r in base_rows),
        sorted(r["id"] for r in opt_rows),
        "裁剪/下推改变了结果集",
    )
    testcase.assertEqual(base_stats.partitions_scanned, len(table.partitions))
    return opt_rows, opt_stats


class TestPruningCounts(unittest.TestCase):
    def setUp(self):
        self.table = make_table()

    def test_eq_prunes_to_one(self):
        _, stats = assert_consistent(self, self.table, Eq("month", 5))
        self.assertEqual(stats.partitions_scanned, 1)
        self.assertEqual(stats.partitions_pruned, 11)
        self.assertEqual(stats.scanned_partition_names, ["p05"])

    def test_range_spans_multiple(self):
        _, stats = assert_consistent(self, self.table, Range("month", 3, 6))
        self.assertEqual(stats.partitions_scanned, 4)

    def test_range_exclusive_bounds(self):
        _, stats = assert_consistent(self, self.table,
                                     Range("month", 3, 6, False, False))
        self.assertEqual(stats.scanned_partition_names, ["p04", "p05"])

    def test_in_list(self):
        _, stats = assert_consistent(self, self.table, In("month", [1, 6, 12]))
        self.assertEqual(stats.partitions_scanned, 3)
        self.assertEqual(stats.scanned_partition_names, ["p01", "p06", "p12"])

    def test_in_list_with_gaps(self):
        _, stats = assert_consistent(self, self.table, In("month", [2, 4, 99]))
        self.assertEqual(stats.scanned_partition_names, ["p02", "p04"])

    def test_no_pruning_without_key_filter(self):
        _, stats = assert_consistent(self, self.table, Range("amount", None, 10))
        self.assertEqual(stats.partitions_scanned, 12)
        self.assertEqual(stats.partitions_pruned, 0)

    def test_prune_everything(self):
        rows, stats = assert_consistent(self, self.table, Eq("month", 99))
        self.assertEqual(stats.partitions_scanned, 0)
        self.assertEqual(stats.rows_scanned, 0)
        self.assertEqual(rows, [])

    def test_and_combination(self):
        cond = And([Range("month", 5, 8), Eq("amount", 8)])
        _, stats = assert_consistent(self, self.table, cond)
        self.assertEqual(stats.partitions_scanned, 4)

    def test_or_combination(self):
        cond = Or([Eq("month", 2), Eq("month", 11)])
        _, stats = assert_consistent(self, self.table, cond)
        self.assertEqual(stats.scanned_partition_names, ["p02", "p11"])

    def test_boundary_first_partition(self):
        _, stats = assert_consistent(self, self.table, Range("month", None, 1))
        self.assertEqual(stats.scanned_partition_names, ["p01"])

    def test_boundary_last_partition(self):
        _, stats = assert_consistent(self, self.table, Range("month", 12, None))
        self.assertEqual(stats.scanned_partition_names, ["p12"])

    def test_boundary_exclusive_out_of_range(self):
        # month > 12：与最后一个分区 [12,12] 的开区间边界不相交
        rows, stats = assert_consistent(
            self, self.table, Range("month", 12, None, lo_inclusive=False))
        self.assertEqual(stats.partitions_scanned, 0)
        self.assertEqual(rows, [])

    def test_boundary_inclusive_edge(self):
        # month >= 12 且 month <= 12：恰好命中边界分区
        cond = And([Range("month", 12, None), Range("month", None, 12)])
        _, stats = assert_consistent(self, self.table, cond)
        self.assertEqual(stats.scanned_partition_names, ["p12"])

    def test_unbounded_range_scans_all(self):
        _, stats = assert_consistent(self, self.table, Range("month"))
        self.assertEqual(stats.partitions_scanned, 12)


class TestPushdownEquivalence(unittest.TestCase):
    """下推前后（pushdown on/off）结果集必须一致。"""

    def setUp(self):
        self.table = make_table(rows_per_partition=20)

    def check_pushdown(self, cond):
        pushed_rows, _ = self.table.query(cond, prune=True, pushdown=True)
        not_pushed_rows, _ = self.table.query(cond, prune=True, pushdown=False)
        self.assertEqual(
            sorted(r["id"] for r in pushed_rows),
            sorted(r["id"] for r in not_pushed_rows),
            "下推改变了结果集",
        )
        return pushed_rows

    def test_pushdown_eq(self):
        self.check_pushdown(Eq("month", 7))

    def test_pushdown_range_and_nonkey(self):
        self.check_pushdown(And([Range("month", 2, 9), Range("amount", 5, 30)]))

    def test_pushdown_in(self):
        self.check_pushdown(In("month", [3, 4, 10]))

    def test_pushdown_no_key_filter(self):
        rows = self.check_pushdown(Eq("amount", 8))
        self.assertTrue(all(r["amount"] == 8 for r in rows))

    def test_pushdown_empty_result(self):
        self.assertEqual(self.check_pushdown(Eq("month", 42)), [])


class TestInterval(unittest.TestCase):
    def test_intersect(self):
        a = Interval(1, True, 5, True)
        b = Interval(3, True, 9, True)
        self.assertEqual(a.intersect(b), Interval(3, True, 5, True))

    def test_intersect_empty(self):
        a = Interval(1, True, 2, True)
        b = Interval(5, True, 9, True)
        self.assertTrue(a.intersect(b).is_empty())

    def test_exclusive_touching_is_empty(self):
        a = Interval(1, True, 3, False)   # [1, 3)
        b = Interval(3, False, 9, True)   # (3, 9]
        self.assertTrue(a.intersect(b).is_empty())

    def test_overlaps_closed(self):
        self.assertTrue(Interval(5, True, 5, True).overlaps_closed(5, 5))
        self.assertFalse(Interval(5, False, 5, True).overlaps_closed(5, 5))
        self.assertFalse(Interval(None, True, 4, True).overlaps_closed(5, 9))
        self.assertTrue(Interval(None, True, None, True).overlaps_closed(5, 9))


class TestTableValidation(unittest.TestCase):
    def test_row_outside_partition_range_rejected(self):
        with self.assertRaises(ValueError):
            PartitionedTable("bad", "month",
                             [Partition("p1", 1, 3, [{"month": 7}])])

    def test_invalid_partition_range_rejected(self):
        with self.assertRaises(ValueError):
            Partition("px", 5, 1, [])


if __name__ == "__main__":
    unittest.main()
