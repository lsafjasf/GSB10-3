"""分区裁剪（partition pruning）与过滤条件下推（predicate pushdown）库。

仅使用 Python 3 标准库。

模型：
- 表按某个分区键（partition key）划分为若干分区，每个分区声明自己覆盖的
  取值区间 [min_value, max_value]（闭区间），并持有该分区的行数据。
- 过滤条件支持三种：等值 eq、范围 range、列表 in，可用 and/or 组合。
- 裁剪：把分区键上的条件折算成取值区间，与每个分区的区间求交，
  不相交的分区整体跳过，完全不读它的行。
- 下推：保留下来的分区在扫描时逐行应用全部过滤条件（包括非分区键条件），
  保证与"全表扫描后再过滤"的结果集完全一致。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 过滤条件
# ---------------------------------------------------------------------------

class Condition:
    """过滤条件基类。子类需实现 match(row) 与 key_interval(key)。"""

    def match(self, row: dict) -> bool:
        raise NotImplementedError

    def key_interval(self, key: str) -> Optional["Interval"]:
        """若该条件约束了分区键 key，返回其取值区间；否则返回 None。"""
        return None

    def key_values(self, key: str) -> Optional[List[Any]]:
        """若该条件是分区键上的 IN 列表，返回候选值列表；否则返回 None。"""
        return None


@dataclass
class Eq(Condition):
    """等值条件：col == value"""
    col: str
    value: Any

    def match(self, row: dict) -> bool:
        return row.get(self.col) == self.value

    def key_interval(self, key: str) -> Optional["Interval"]:
        if self.col == key:
            return Interval(self.value, True, self.value, True)
        return None


@dataclass
class Range(Condition):
    """范围条件：lo (</<=) col (</<=) hi，None 表示该侧无界。"""
    col: str
    lo: Any = None
    hi: Any = None
    lo_inclusive: bool = True
    hi_inclusive: bool = True

    def match(self, row: dict) -> bool:
        v = row.get(self.col)
        if v is None:
            return False
        if self.lo is not None:
            if v < self.lo or (v == self.lo and not self.lo_inclusive):
                return False
        if self.hi is not None:
            if v > self.hi or (v == self.hi and not self.hi_inclusive):
                return False
        return True

    def key_interval(self, key: str) -> Optional["Interval"]:
        if self.col == key:
            return Interval(self.lo, self.lo_inclusive, self.hi, self.hi_inclusive)
        return None


@dataclass
class In(Condition):
    """列表条件：col IN (values...)"""
    col: str
    values: Sequence[Any]

    def match(self, row: dict) -> bool:
        return row.get(self.col) in self.values

    def key_values(self, key: str) -> Optional[List[Any]]:
        if self.col == key:
            return list(self.values)
        return None


@dataclass
class And(Condition):
    conditions: List[Condition]

    def match(self, row: dict) -> bool:
        return all(c.match(row) for c in self.conditions)

    def key_interval(self, key: str) -> Optional["Interval"]:
        """AND 下各子条件的区间取交集。"""
        result: Optional[Interval] = None
        for c in self.conditions:
            iv = c.key_interval(key)
            if iv is None:
                continue
            result = iv if result is None else result.intersect(iv)
            if result is not None and result.is_empty():
                return result
        return result

    def key_values(self, key: str) -> Optional[List[Any]]:
        for c in self.conditions:
            vals = c.key_values(key)
            if vals is not None:
                return vals
        return None


@dataclass
class Or(Condition):
    conditions: List[Condition]

    def match(self, row: dict) -> bool:
        return any(c.match(row) for c in self.conditions)


# ---------------------------------------------------------------------------
# 取值区间
# ---------------------------------------------------------------------------

@dataclass
class Interval:
    """区间 [lo, hi]，端点可开可闭，None 表示无界。"""
    lo: Any = None
    lo_inclusive: bool = True
    hi: Any = None
    hi_inclusive: bool = True

    def is_empty(self) -> bool:
        if self.lo is None or self.hi is None:
            return False
        if self.lo > self.hi:
            return True
        if self.lo == self.hi and not (self.lo_inclusive and self.hi_inclusive):
            return True
        return False

    def intersect(self, other: "Interval") -> "Interval":
        lo, lo_inc = self.lo, self.lo_inclusive
        if other.lo is not None and (lo is None or other.lo > lo):
            lo, lo_inc = other.lo, other.lo_inclusive
        elif other.lo is not None and other.lo == lo:
            lo_inc = lo_inc and other.lo_inclusive
        hi, hi_inc = self.hi, self.hi_inclusive
        if other.hi is not None and (hi is None or other.hi < hi):
            hi, hi_inc = other.hi, other.hi_inclusive
        elif other.hi is not None and other.hi == hi:
            hi_inc = hi_inc and other.hi_inclusive
        return Interval(lo, lo_inc, hi, hi_inc)

    def overlaps_closed(self, pmin: Any, pmax: Any) -> bool:
        """判断本区间是否与闭区间 [pmin, pmax]（分区区间）有交。"""
        if self.is_empty():
            return False
        if self.hi is not None:
            if self.hi < pmin or (self.hi == pmin and not self.hi_inclusive):
                return False
        if self.lo is not None:
            if self.lo > pmax or (self.lo == pmax and not self.lo_inclusive):
                return False
        return True

    def contains(self, v: Any) -> bool:
        if self.lo is not None and (v < self.lo or (v == self.lo and not self.lo_inclusive)):
            return False
        if self.hi is not None and (v > self.hi or (v == self.hi and not self.hi_inclusive)):
            return False
        return True


# ---------------------------------------------------------------------------
# 分区与分区表
# ---------------------------------------------------------------------------

@dataclass
class Partition:
    """一个分区：覆盖分区键闭区间 [min_value, max_value]，持有若干行。"""
    name: str
    min_value: Any
    max_value: Any
    rows: List[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.min_value > self.max_value:
            raise ValueError(f"分区 {self.name} 区间非法: {self.min_value} > {self.max_value}")


@dataclass
class ScanStats:
    partitions_total: int = 0
    partitions_scanned: int = 0
    partitions_pruned: int = 0
    rows_scanned: int = 0
    scanned_partition_names: List[str] = field(default_factory=list)


class PartitionedTable:
    def __init__(self, name: str, partition_key: str, partitions: List[Partition]):
        self.name = name
        self.partition_key = partition_key
        self.partitions = list(partitions)
        for p in self.partitions:
            for row in p.rows:
                v = row.get(partition_key)
                if v is None or not (p.min_value <= v <= p.max_value):
                    raise ValueError(
                        f"行 {row} 的分区键 {partition_key}={v} 不在分区 "
                        f"{p.name} 的区间 [{p.min_value}, {p.max_value}] 内"
                    )

    # -- 裁剪 ---------------------------------------------------------------

    def prune(self, condition: Condition) -> List[Partition]:
        """根据条件裁剪分区，返回需要扫描的分区列表。"""
        kept = []
        for p in self.partitions:
            if self._partition_may_match(p, condition):
                kept.append(p)
        return kept

    def _partition_may_match(self, p: Partition, cond: Condition) -> bool:
        # OR：任一分支可能命中即保留（保守，不错裁）
        if isinstance(cond, Or):
            return any(self._partition_may_match(p, c) for c in cond.conditions)
        # IN 列表：任一候选值落在分区区间内即保留
        values = cond.key_values(self.partition_key)
        if values is not None:
            return any(p.min_value <= v <= p.max_value for v in values)
        # 等值/范围：折算为区间，与分区区间求交
        interval = cond.key_interval(self.partition_key)
        if interval is not None:
            return interval.overlaps_closed(p.min_value, p.max_value)
        # 条件不含分区键：无法裁剪
        return True

    # -- 查询 ---------------------------------------------------------------

    def query(self, condition: Condition,
              prune: bool = True, pushdown: bool = True
              ) -> Tuple[List[dict], ScanStats]:
        """执行查询。

        prune=False 时扫描全部分区（基线）；pushdown=False 时在分区扫描阶段
        不应用过滤、把全部行读上来之后再统一过滤（结果一致，仅扫描量不同）。
        返回 (结果行, 扫描统计)。
        """
        stats = ScanStats(partitions_total=len(self.partitions))
        candidates = self.prune(condition) if prune else list(self.partitions)
        stats.partitions_scanned = len(candidates)
        stats.partitions_pruned = stats.partitions_total - stats.partitions_scanned
        stats.scanned_partition_names = [p.name for p in candidates]

        result: List[dict] = []
        for p in candidates:
            stats.rows_scanned += len(p.rows)
            if pushdown:
                # 条件下推：在分区内部逐行过滤
                result.extend(row for row in p.rows if condition.match(row))
            else:
                result.extend(p.rows)
        if not pushdown:
            result = [row for row in result if condition.match(row)]
        return result, stats
