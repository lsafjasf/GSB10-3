"""partition_pruning 的自测与对比数据输出。

运行：
    python3 test_partition_pruning.py          # 跑全部用例 + 打印对比数据
    python3 -m unittest test_partition_pruning -v
"""

import calendar
import random
import unittest

from partition_pruning import (
    And, Eq, In, Partition, PartitionedTable, Range,
)

PARTITION_KEY = "dt"


def build_table(rows_per_partition=200, seed=42):
    """构造 2024 年 12 个月度分区，每月 rows_per_partition 行，确定性数据。"""
    rng = random.Random(seed)
    partitions = []
    for month in range(1, 13):
        ndays = calendar.monthrange(2024, month)[1]
        lo = f"2024-{month:02d}-01"
        hi = f"2024-{month:02d}-{ndays:02d}"
        rows = []
        for i in range(rows_per_partition):
            day = rng.randint(1, ndays)
            rows.append({
                "dt": f"2024-{month:02d}-{day:02d}",
                "user": f"u{rng.randint(1, 50)}",
                "amount": rng.randint(1, 1000),
            })
        # 保证每个分区都包含首末日，便于边界用例。
        rows.append({"dt": lo, "user": "u1", "amount": 5})
        rows.append({"dt": hi, "user": "u2", "amount": 6})
        partitions.append(Partition(f"p2024{month:02d}", lo, hi, rows))
    return PartitionedTable("events", PARTITION_KEY, partitions)


def sort_key(row):
    return (row["dt"], row["user"], row["amount"])


class PruningCorrectnessTest(unittest.TestCase):
    """裁剪结果必须与逐分区全扫描一致；下推前后结果必须一致。"""

    @classmethod
    def setUpClass(cls):
        cls.table = build_table()

    def assert_consistent(self, cond):
        t = self.table
        pruned_rows, pruned_stats = t.query_pruned_pushdown(cond)
        full_rows, full_stats = t.query_full_scan_pushdown(cond)
        base_rows, base_stats = t.query_no_pushdown(cond)

        # 1) 裁剪 + 下推 == 逐分区全扫描（下推）
        self.assertEqual(
            sorted(map(str, pruned_rows)), sorted(map(str, full_rows)),
            "裁剪后结果与逐分区全扫描不一致")
        # 2) 下推到分区内部 == 汇总后再过滤（下推前后对拍）
        self.assertEqual(
            sorted(map(str, pruned_rows)), sorted(map(str, base_rows)),
            "下推前后结果集不一致")
        # 3) 裁剪不能增加扫描量
        self.assertLessEqual(pruned_stats.rows_scanned, full_stats.rows_scanned)
        return pruned_rows, pruned_stats, full_stats

    # ---------------- 三种条件类型 ----------------

    def test_eq_single_partition(self):
        cond = Eq("dt", "2024-03-15")
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.partitions_scanned, 1)
        self.assertEqual(pruned.scanned_partition_names, ("p202403",))
        self.assertTrue(all(r["dt"] == "2024-03-15" for r in rows))
        self.assertGreater(len(rows), 0)

    def test_range_spans_multiple_partitions(self):
        cond = Range("dt", "2024-03-15", "2024-06-10")
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.partitions_scanned, 4)  # 3,4,5,6 月
        self.assertEqual(pruned.scanned_partition_names,
                         ("p202403", "p202404", "p202405", "p202406"))
        self.assertTrue(all("2024-03-15" <= r["dt"] <= "2024-06-10" for r in rows))

    def test_in_list(self):
        cond = In("dt", ["2024-01-01", "2024-12-31", "2025-01-01"])
        rows, pruned, full = self.assert_consistent(cond)
        # 2025-01-01 不在任何分区，应裁到只剩 1 月和 12 月
        self.assertEqual(pruned.scanned_partition_names, ("p202401", "p202412"))
        self.assertTrue(all(r["dt"] in ("2024-01-01", "2024-12-31") for r in rows))
        # 构造时保证首末日各至少一行；随机行也可能恰好落在首末日
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn("2024-01-01", {r["dt"] for r in rows})
        self.assertIn("2024-12-31", {r["dt"] for r in rows})

    def test_and_combination(self):
        cond = And(Range("dt", "2024-02-01", "2024-04-30"),
                   Eq("user", "u7"))
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.scanned_partition_names,
                         ("p202402", "p202403", "p202404"))
        self.assertTrue(all(r["user"] == "u7" for r in rows))

    # ---------------- 边界与特殊情形 ----------------

    def test_no_pruning_range_covers_all(self):
        cond = Range("dt", "2024-01-01", "2024-12-31")
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.partitions_scanned, 12)

    def test_prune_everything(self):
        cond = Eq("dt", "2025-06-01")   # 超出所有分区
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.partitions_scanned, 0)
        self.assertEqual(rows, [])

    def test_prune_everything_range_gap(self):
        # 落在两个分区之间的“空隙”不存在（分区连续），改用全年之外的范围
        cond = Range("dt", "2023-01-01", "2023-12-31")
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.partitions_scanned, 0)
        self.assertEqual(rows, [])

    def test_no_filter_on_partition_key(self):
        # 只过滤非分区键：无法裁剪，必须扫全部 12 个分区，结果仍须一致
        cond = Range("amount", 500, 510)
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.partitions_scanned, 12)
        self.assertTrue(all(500 <= r["amount"] <= 510 for r in rows))

    def test_boundary_inclusive(self):
        # 上界恰好是 3 月分区最后一天（闭区间）：3 月必须保留，4 月裁掉
        cond = Range("dt", "2024-03-01", "2024-03-31",
                     low_inclusive=True, high_inclusive=True)
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.scanned_partition_names, ("p202403",))
        self.assertIn("2024-03-31", {r["dt"] for r in rows})

    def test_boundary_exclusive(self):
        # 下界开区间 (2024-03-31, ...]：3 月分区 [03-01, 03-31] 与条件
        # 仅在 03-31 处相接但为开区间，应裁掉 3 月
        cond = Range("dt", "2024-03-31", "2024-04-02",
                     low_inclusive=False, high_inclusive=True)
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.scanned_partition_names, ("p202404",))
        self.assertTrue(all(r["dt"] > "2024-03-31" for r in rows))

    def test_boundary_touch_single_point(self):
        # 条件下界 == 分区上界（闭）：仅相交于一点，分区必须保留
        cond = Range("dt", "2024-05-31", "2024-12-31")
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.partitions_scanned, 8)  # 5..12 月
        self.assertIn("p202405", pruned.scanned_partition_names)

    def test_eq_on_partition_boundary(self):
        cond = Eq("dt", "2024-06-30")   # 6 月分区最后一天
        rows, pruned, full = self.assert_consistent(cond)
        self.assertEqual(pruned.scanned_partition_names, ("p202406",))

    # ---------------- 随机对拍 ----------------

    def test_randomized_conditions(self):
        rng = random.Random(7)
        for _ in range(200):
            kind = rng.choice(["eq", "range", "in", "and", "nokey"])
            if kind == "eq":
                m = rng.randint(1, 14)  # 故意越界到 13/14 月
                d = rng.randint(1, 28)
                cond = Eq("dt", f"2024-{min(m,12):02d}-{d:02d}" if m <= 12
                          else f"2025-01-{d:02d}")
            elif kind == "range":
                a = f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
                b = f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
                lo, hi = min(a, b), max(a, b)
                cond = Range("dt", lo, hi,
                             low_inclusive=rng.random() < 0.5,
                             high_inclusive=rng.random() < 0.5)
            elif kind == "in":
                vals = [f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
                        for _ in range(rng.randint(1, 5))]
                cond = In("dt", vals)
            elif kind == "and":
                cond = And(
                    Range("dt", f"2024-{rng.randint(1,12):02d}-01", "2024-12-31"),
                    Eq("user", f"u{rng.randint(1,50)}"))
            else:
                cond = Range("amount", rng.randint(1, 500), rng.randint(501, 1000))
            self.assert_consistent(cond)


class ComparisonReport(unittest.TestCase):
    """输出裁剪前后扫描量对比数据（作为交付数据，同时校验一致性）。"""

    def test_report(self):
        table = build_table()
        cases = [
            ("等值 dt='2024-03-15'", Eq("dt", "2024-03-15")),
            ("范围 03-15~06-10", Range("dt", "2024-03-15", "2024-06-10")),
            ("列表 IN(01-01,12-31,2025-01-01)",
             In("dt", ["2024-01-01", "2024-12-31", "2025-01-01"])),
            ("组合 范围+user", And(Range("dt", "2024-02-01", "2024-04-30"),
                                   Eq("user", "u7"))),
            ("无裁剪(全年)", Range("dt", "2024-01-01", "2024-12-31")),
            ("裁掉全部(2025-06-01)", Eq("dt", "2025-06-01")),
            ("非分区键过滤(amount)", Range("amount", 500, 510)),
        ]
        lines = []
        header = f"{'查询':<32}{'裁剪前分区':>10}{'裁剪后分区':>10}{'裁剪前行数':>10}{'裁剪后行数':>10}{'结果行数':>8}{'一致':>6}"
        lines.append(header)
        for label, cond in cases:
            pruned_rows, ps = table.query_pruned_pushdown(cond)
            full_rows, fs = table.query_full_scan_pushdown(cond)
            base_rows, _ = table.query_no_pushdown(cond)
            ok = (sorted(map(str, pruned_rows)) == sorted(map(str, full_rows))
                  == sorted(map(str, base_rows)))
            self.assertTrue(ok, f"{label} 结果不一致")
            lines.append(
                f"{label:<32}{fs.partitions_scanned:>10}{ps.partitions_scanned:>10}"
                f"{fs.rows_scanned:>10}{ps.rows_scanned:>10}{len(pruned_rows):>8}"
                f"{'OK' if ok else 'FAIL':>6}")
        report = "\n".join(lines)
        print("\n===== 裁剪对比数据（12 个分区，每分区 202 行）=====")
        print(report)
        print("说明：裁剪后行数 = 实际逐行检查的行数；'一致' 表示 裁剪+下推 / "
              "全扫+下推 / 全扫后过滤 三条路径结果完全相同。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
