"""ipsetx: IP 地址段（CIDR）集合的合并与快速查找。

仅使用 Python 标准库（ipaddress / bisect）。

功能
----
1. 合并：把一组带优先级的 CIDR 段合并为互不重叠、互不相邻的最简 CIDR 集合，
   覆盖三种关系：
     - 完全相同：去重，保留最高优先级；
     - 包含：小段被大段吸收，合并后段保留参与合并各段的最高优先级；
     - 相邻：首尾相接的段合并（能聚成单个 CIDR 就聚合，否则用最少 CIDR 覆盖）。
   IPv4 与 IPv6 分开处理，绝不互相混算。
2. 查找：基于有序区间 + 二分查找，单次查询 O(log n)，不会退化为线性扫描。

优先级约定：数值越小优先级越高；多段合并后取其中的最高优先级（最小数值）。
"""

from __future__ import annotations

import ipaddress
from bisect import bisect_right
from typing import Iterable, List, Optional, Sequence, Tuple, Union

__all__ = ["IPSet", "QueryResult", "MergedSegment", "merge_segments"]

# 查询未命中时的统一标记
MISS = "MISS"

AddressLike = Union[str, int, ipaddress.IPv4Address, ipaddress.IPv6Address]
NetworkLike = Union[str, ipaddress.IPv4Network, ipaddress.IPv6Network]


class MergedSegment:
    """合并后的一个地址段。"""

    __slots__ = ("network", "priority")

    def __init__(self, network: NetworkLike, priority: int):
        if isinstance(network, str):
            network = ipaddress.ip_network(network, strict=False)
        self.network = network
        self.priority = int(priority)

    @property
    def cidr(self) -> str:
        return str(self.network)

    @property
    def version(self) -> int:
        return self.network.version

    def to_dict(self) -> dict:
        return {"cidr": self.cidr, "priority": self.priority, "version": self.version}

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"MergedSegment({self.cidr}, priority={self.priority})"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, MergedSegment)
            and self.network == other.network
            and self.priority == other.priority
        )


class QueryResult:
    """一次查询的结果。未命中时 hit 为 False、segment/priority 为 None。"""

    __slots__ = ("hit", "segment", "priority", "version")

    def __init__(self, hit: bool, segment: Optional[str], priority: Optional[int], version: int):
        self.hit = hit
        self.segment = segment
        self.priority = priority
        self.version = version

    def to_dict(self) -> dict:
        if not self.hit:
            return {"hit": False, "result": MISS, "version": self.version}
        return {
            "hit": True,
            "segment": self.segment,
            "priority": self.priority,
            "version": self.version,
        }

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        if not self.hit:
            return f"QueryResult(MISS, version={self.version})"
        return f"QueryResult({self.segment}, priority={self.priority})"


def _parse_entry(entry) -> Tuple[object, int]:
    """把一条输入规范化为 (IPv4Network/IPv6Network, priority)。

    支持的输入形式：
      - {"cidr": "10.0.0.0/24", "priority": 5}
      - ("10.0.0.0/24", 5)
      - 任意带 .cidr/.priority 属性的对象
    """
    if isinstance(entry, dict):
        cidr, priority = entry["cidr"], entry["priority"]
    elif isinstance(entry, (tuple, list)) and len(entry) == 2:
        cidr, priority = entry
    else:
        cidr, priority = entry.cidr, entry.priority
    if isinstance(cidr, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        net = cidr
    else:
        # strict=False：允许 10.0.0.1/24 这类带主机位的写法，自动归一化为网段
        net = ipaddress.ip_network(cidr, strict=False)
    return net, int(priority)


def _range_to_segments(start: int, end: int, priority: int, version: int) -> List[MergedSegment]:
    addr_cls = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
    first = addr_cls(start)
    last = addr_cls(end)
    return [
        MergedSegment(net, priority)
        for net in ipaddress.summarize_address_range(first, last)
    ]


def _merge_family(records: List[Tuple[object, int]]) -> List[MergedSegment]:
    """合并同一地址族（同为 v4 或同为 v6）的段。

    先按起始地址排序做区间扫描（重叠、包含、相邻都并入当前区间，
    优先级取最小数值），再把每个合并区间用 summarize_address_range
    切成最少的标准 CIDR。
    """
    if not records:
        return []
    version = records[0][0].version
    items = sorted(
        ((int(net.network_address), int(net.broadcast_address), prio) for net, prio in records),
        key=lambda t: (t[0], t[1]),
    )
    merged: List[MergedSegment] = []
    cur_start, cur_end, cur_prio = items[0]
    for start, end, prio in items[1:]:
        if start <= cur_end + 1:  # 重叠、包含或相邻
            if end > cur_end:
                cur_end = end
            if prio < cur_prio:
                cur_prio = prio
        else:
            merged.extend(_range_to_segments(cur_start, cur_end, cur_prio, version))
            cur_start, cur_end, cur_prio = start, end, prio
    merged.extend(_range_to_segments(cur_start, cur_end, cur_prio, version))
    return merged


def merge_segments(entries: Iterable) -> List[MergedSegment]:
    """函数式接口：输入若干段，返回合并后的段列表（v4 在前、各自按地址排序）。"""
    return IPSet(entries).segments


class _Index:
    """单地址族的有序区间索引，查询为二分查找 O(log n)。"""

    __slots__ = ("starts", "ends", "cidrs", "priorities")

    def __init__(self, segments: Sequence[MergedSegment]):
        self.starts = [int(s.network.network_address) for s in segments]
        self.ends = [int(s.network.broadcast_address) for s in segments]
        self.cidrs = [str(s.network) for s in segments]
        self.priorities = [s.priority for s in segments]

    def __len__(self) -> int:
        return len(self.starts)

    def lookup(self, value: int) -> Optional[Tuple[str, int]]:
        starts = self.starts
        i = bisect_right(starts, value) - 1
        if i >= 0 and value <= self.ends[i]:
            return self.cidrs[i], self.priorities[i]
        return None


class IPSet:
    """地址段集合：构建时合并，之后可反复 O(log n) 查询。"""

    def __init__(self, entries: Iterable = ()):  # entries 见 _parse_entry
        v4: List[Tuple[object, int]] = []
        v6: List[Tuple[object, int]] = []
        for entry in entries:
            net, prio = _parse_entry(entry)
            (v4 if net.version == 4 else v6).append((net, prio))
        self._v4_segments = _merge_family(v4)
        self._v6_segments = _merge_family(v6)
        self._v4_index = _Index(self._v4_segments)
        self._v6_index = _Index(self._v6_segments)

    @classmethod
    def from_segments(cls, segments: Iterable[MergedSegment]) -> "IPSet":
        """由已合并的段直接构建（跳过合并，用于基准测试不同规模）。"""
        obj = cls.__new__(cls)
        segs = list(segments)
        obj._v4_segments = sorted(
            (s for s in segs if s.version == 4),
            key=lambda s: int(s.network.network_address),
        )
        obj._v6_segments = sorted(
            (s for s in segs if s.version == 6),
            key=lambda s: int(s.network.network_address),
        )
        obj._v4_index = _Index(obj._v4_segments)
        obj._v6_index = _Index(obj._v6_segments)
        return obj

    # ---- 查询 ----

    def query(self, address: AddressLike) -> QueryResult:
        """查询地址命中的段与优先级；未命中返回 hit=False 的明确结果。"""
        if isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            addr = address
        else:
            addr = ipaddress.ip_address(address)
        index = self._v4_index if addr.version == 4 else self._v6_index
        found = index.lookup(int(addr))
        if found is None:
            return QueryResult(False, None, None, addr.version)
        return QueryResult(True, found[0], found[1], addr.version)

    def match(self, address: AddressLike) -> Optional[Tuple[str, int]]:
        """轻量接口：命中返回 (cidr, priority)，未命中返回 None。"""
        if isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            addr = address
        else:
            addr = ipaddress.ip_address(address)
        index = self._v4_index if addr.version == 4 else self._v6_index
        return index.lookup(int(addr))

    def __contains__(self, address: AddressLike) -> bool:
        return self.match(address) is not None

    # ---- 合并结果 ----

    @property
    def segments(self) -> List[MergedSegment]:
        """全部合并段，v4 在前，各自按地址升序。"""
        return list(self._v4_segments) + list(self._v6_segments)

    def segments_v4(self) -> List[MergedSegment]:
        return list(self._v4_segments)

    def segments_v6(self) -> List[MergedSegment]:
        return list(self._v6_segments)

    def stats(self) -> dict:
        return {
            "ipv4_segments": len(self._v4_segments),
            "ipv6_segments": len(self._v6_segments),
            "total": len(self._v4_segments) + len(self._v6_segments),
        }

    def __len__(self) -> int:
        return len(self._v4_segments) + len(self._v6_segments)
