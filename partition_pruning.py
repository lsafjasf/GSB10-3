"""分区裁剪与过滤条件下推（仅标准库）。

模型：
- 一张表按某个分区键（如日期列 dt）划分为若干分区，每个分区持有
  一个闭区间 [lo, hi] 与落在该区间内的行。
- 查询条件支持：等值 Eq、范围 Range、列表 In，以及 And 组合。
- 裁剪（pruning）：利用分区键区间与条件的交集判断，跳过不可能
  命中的分区。
- 下推（pushdown）：把过滤条件放到每个分区内部逐行执行，而不是
  先把所有分区的数据汇总后再过滤。

一致性保证（由自测验证）：
- 裁剪后的扫描结果 == 逐分区全扫描的结果。
- 下推到分区内部过滤的结果 == 汇总后再过滤的结果。
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------- 条件

class Condition:
    """查询条件基类。"""

    def match(self, row):
        """逐行判断（下推后在分区内部执行）。"""
        raise NotImplementedError

    def may_overlap(self, lo, hi, partition_key):
        """保守判断：分区闭区间 [lo, hi] 内是否可能存在满足条件的行。

        返回 False 才能安全地裁掉该分区；不确定时必须返回 True。
        """
        return True


@dataclass(frozen=True)
class Eq(Condition):
    key: str
    value: object

    def match(self, row):
        return row.get(self.key) == self.value

    def may_overlap(self, lo, hi, partition_key):
        if self.key != partition_key:
            return True
        return lo <= self.value <= hi


@dataclass(frozen=True)
class Range(Condition):
    key: str
    low: object = None          # None 表示无下界
    high: object = None         # None 表示无上界
    low_inclusive: bool = True
    high_inclusive: bool = True

    def match(self, row):
        v = row.get(self.key)
        if v is None:
            return False
        if self.low is not None:
            if v < self.low or (v == self.low and not self.low_inclusive):
                return False
        if self.high is not None:
            if v > self.high or (v == self.high and not self.high_inclusive):
                return False
        return True

    def may_overlap(self, lo, hi, partition_key):
        if self.key != partition_key:
            return True
        # 区间相交判定：[lo, hi] 与条件区间的交集非空。
        if self.high is not None:
            if lo > self.high:
                return False
            if lo == self.high and not self.high_inclusive:
                return False
        if self.low is not None:
            if hi < self.low:
                return False
            if hi == self.low and not self.low_inclusive:
                return False
        return True


@dataclass(frozen=True)
class In(Condition):
    key: str
    values: tuple

    def __init__(self, key, values):
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "values", tuple(values))

    def match(self, row):
        return row.get(self.key) in self.values

    def may_overlap(self, lo, hi, partition_key):
        if self.key != partition_key:
            return True
        return any(lo <= v <= hi for v in self.values)


@dataclass(frozen=True)
class And(Condition):
    conditions: tuple

    def __init__(self, *conditions):
        object.__setattr__(self, "conditions", tuple(conditions))

    def match(self, row):
        return all(c.match(row) for c in self.conditions)

    def may_overlap(self, lo, hi, partition_key):
        # 所有子条件都可能命中才保留（保守：任一子条件说不可能即可裁掉）。
        return all(c.may_overlap(lo, hi, partition_key) for c in self.conditions)


# ---------------------------------------------------------------- 分区与表

@dataclass
class Partition:
    """一个分区：分区键闭区间 [lo, hi] + 该区间内的行。"""
    name: str
    lo: object
    hi: object
    rows: list = field(default_factory=list)

    def scan(self, condition=None):
        """扫描分区；若给定条件则在分区内部逐行过滤（即条件下推）。"""
        if condition is None:
            return list(self.rows)
        return [r for r in self.rows if condition.match(r)]


@dataclass
class ScanStats:
    partitions_total: int
    partitions_scanned: int
    rows_scanned: int          # 实际被逐行检查的行数
    scanned_partition_names: tuple = ()


class PartitionedTable:
    def __init__(self, name, partition_key, partitions):
        self.name = name
        self.partition_key = partition_key
        self.partitions = list(partitions)

    # ---------------- 裁剪 ----------------

    def prune(self, condition):
        """返回裁剪后需要扫描的分区列表。"""
        return [
            p for p in self.partitions
            if condition.may_overlap(p.lo, p.hi, self.partition_key)
        ]

    # ---------------- 查询路径 ----------------

    def query_pruned_pushdown(self, condition):
        """裁剪 + 下推：先裁分区，再在每个分区内逐行过滤。"""
        selected = self.prune(condition)
        rows, rows_scanned = [], 0
        for p in selected:
            rows_scanned += len(p.rows)
            rows.extend(p.scan(condition))     # 条件下推到分区内部
        stats = ScanStats(
            partitions_total=len(self.partitions),
            partitions_scanned=len(selected),
            rows_scanned=rows_scanned,
            scanned_partition_names=tuple(p.name for p in selected),
        )
        return rows, stats

    def query_full_scan_pushdown(self, condition):
        """不裁剪，但条件下推到每个分区（用于验证裁剪正确性）。"""
        rows, rows_scanned = [], 0
        for p in self.partitions:
            rows_scanned += len(p.rows)
            rows.extend(p.scan(condition))
        stats = ScanStats(
            partitions_total=len(self.partitions),
            partitions_scanned=len(self.partitions),
            rows_scanned=rows_scanned,
            scanned_partition_names=tuple(p.name for p in self.partitions),
        )
        return rows, stats

    def query_no_pushdown(self, condition):
        """不裁剪也不下推：把所有分区数据汇总后再统一过滤（对拍基准）。"""
        all_rows = []
        for p in self.partitions:
            all_rows.extend(p.scan())          # 不过滤，整分区读出
        rows = [r for r in all_rows if condition.match(r)]
        stats = ScanStats(
            partitions_total=len(self.partitions),
            partitions_scanned=len(self.partitions),
            rows_scanned=len(all_rows),
            scanned_partition_names=tuple(p.name for p in self.partitions),
        )
        return rows, stats
