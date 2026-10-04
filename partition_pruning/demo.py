"""演示：分区裁剪 + 条件下推 与 全表扫描基线 的对比。

运行：python3 demo.py
输出：每个查询的扫描分区数对比、结果集对拍（prune+pushdown vs 基线）。
"""

from pruning import And, Eq, In, Or, Partition, PartitionedTable, Range


def build_table() -> PartitionedTable:
    """按月分区（month: 1..12），每月一个分区，每月 10 行。"""
    partitions = []
    for month in range(1, 13):
        rows = [
            {
                "id": month * 100 + i,
                "month": month,
                "amount": (month * 7 + i * 13) % 100,
                "region": ["east", "west", "north", "south"][i % 4],
            }
            for i in range(10)
        ]
        partitions.append(Partition(f"p{month:02d}", month, month, rows))
    return PartitionedTable("sales", "month", partitions)


QUERIES = [
    ("等值: month = 3",                Eq("month", 3)),
    ("范围: 3 <= month <= 6",          Range("month", 3, 6)),
    ("范围(开): 3 < month < 6",        Range("month", 3, 6, False, False)),
    ("列表: month IN (1, 6, 12)",      In("month", [1, 6, 12])),
    ("组合: month>=5 AND month<=8 AND region='east'",
     And([Range("month", 5, 8), Eq("region", "east")])),
    ("OR: month=2 OR month=11",        Or([Eq("month", 2), Eq("month", 11)])),
    ("无裁剪: 只过滤非分区键 amount<10", Range("amount", None, 10, hi_inclusive=False)),
    ("裁掉全部: month = 99",           Eq("month", 99)),
    ("边界: month <= 1",               Range("month", None, 1)),
    ("边界: month >= 12",              Range("month", 12, None)),
    ("边界: 12 < month(开区间，越界)",  Range("month", 12, None, lo_inclusive=False)),
]


def main() -> None:
    table = build_table()
    total_rows = sum(len(p.rows) for p in table.partitions)
    print(f"表 {table.name}: {len(table.partitions)} 个分区, 共 {total_rows} 行\n")
    header = f"{'查询':<38} {'基线扫描分区':>12} {'裁剪后扫描分区':>14} {'结果行数':>8} {'对拍':>6}"
    print(header)
    print("-" * len(header))
    all_ok = True
    for label, cond in QUERIES:
        # 基线：不裁剪、不下推（全分区扫描，读全部行后统一过滤）
        base_rows, base_stats = table.query(cond, prune=False, pushdown=False)
        # 优化：裁剪 + 下推
        opt_rows, opt_stats = table.query(cond, prune=True, pushdown=True)
        # 对拍：结果集必须一致（按 id 排序后比较，消除顺序差异）
        same = sorted(r["id"] for r in base_rows) == sorted(r["id"] for r in opt_rows)
        all_ok = all_ok and same
        print(f"{label:<38} {base_stats.partitions_scanned:>12} "
              f"{opt_stats.partitions_scanned:>14} {len(opt_rows):>8} "
              f"{'OK' if same else 'FAIL':>6}")
        if not same:
            print(f"  基线: {sorted(r['id'] for r in base_rows)}")
            print(f"  优化: {sorted(r['id'] for r in opt_rows)}")
    print("-" * len(header))
    print("全部对拍通过" if all_ok else "存在对拍失败！")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
