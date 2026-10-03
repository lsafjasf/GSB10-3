"""IP 地址段（CIDR）合并与快速查找库，仅依赖 Python 标准库。

功能：
- merge_networks: 合并相邻 / 包含 / 完全相同的地址段，IPv4 与 IPv6 分别处理。
- AddressSet:     构建有序区间表，lookup 用二分查找定位命中段及其优先级。

优先级语义：
- add() 时可显式传入 priority（数值越大优先级越高）；
- 缺省时取该段的前缀长度（越具体的段优先级越高）；
- 多个原始段被合并成一个大段时，大段优先级取被合并段中的最大值。
"""

from __future__ import annotations

import bisect
import ipaddress
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple, Union

Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
Address = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


def parse_network(value: Union[str, Network]) -> Network:
    """把字符串或网络对象统一解析为 IPv4Network / IPv6Network。

    strict=False：允许 "10.0.0.1/24" 这类带主机位的写法，自动归一到网段。
    """
    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return value
    return ipaddress.ip_network(value, strict=False)


def parse_address(value: Union[str, Address, int]) -> Address:
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return value
    return ipaddress.ip_address(value)


def merge_networks(networks: Iterable[Union[str, Network]]) -> List[Network]:
    """合并地址段，覆盖相邻、包含、完全相同三种情况。

    IPv4 与 IPv6 分别合并，绝不互相混算（collapse_addresses 对混合版本
    会直接抛 TypeError，因此这里先按版本分组）。
    返回按 (版本, 地址) 排序后的段列表。
    """
    v4: List[Network] = []
    v6: List[Network] = []
    for item in networks:
        net = parse_network(item)
        (v4 if net.version == 4 else v6).append(net)
    merged = list(ipaddress.collapse_addresses(v4))
    merged += list(ipaddress.collapse_addresses(v6))
    return merged


@dataclass(frozen=True)
class LookupResult:
    """查询结果。hit=False 表示未命中，此时 network/priority 为 None。"""

    hit: bool
    network: Optional[Network] = None
    priority: Optional[int] = None
    version: Optional[int] = None

    def __str__(self) -> str:
        if not self.hit:
            return f"MISS (version=IPv{self.version})"
        return f"HIT {self.network} priority={self.priority}"


class AddressSet:
    """地址段集合：先合并，再对合并后的有序区间做二分查找。

    合并后的段两两不相交，因此任一地址至多命中一个段，
    lookup 复杂度 O(log N)，不会退化为线性扫描。
    """

    def __init__(self, entries: Iterable[Union[str, Tuple[str, int]]] = ()):  # noqa: B008
        self._entries: List[Tuple[Network, int]] = []
        # 每个版本的查找表：(starts, ends, networks, priorities)，懒构建
        self._tables = {4: None, 6: None}
        for entry in entries:
            if isinstance(entry, tuple):
                self.add(entry[0], priority=entry[1])
            else:
                self.add(entry)

    def add(self, cidr: Union[str, Network], priority: Optional[int] = None) -> None:
        net = parse_network(cidr)
        if priority is None:
            priority = net.prefixlen
        self._entries.append((net, int(priority)))
        self._tables[net.version] = None  # 使缓存失效

    def __len__(self) -> int:
        return len(self._entries)

    def _build_version(self, version: int):
        nets = [(n, p) for (n, p) in self._entries if n.version == version]
        collapsed = list(ipaddress.collapse_addresses([n for n, _ in nets]))
        starts = [int(n.network_address) for n in collapsed]
        ends = [int(n.broadcast_address) for n in collapsed]
        # 每个合并段的优先级 = 被它吸收的原始段中的最大优先级。
        # 合并段互不相交，用二分把每个原始段归到包含它的合并段上，O(N log M)。
        prios: List[Optional[int]] = [None] * len(collapsed)
        for net, prio in nets:
            idx = bisect.bisect_right(starts, int(net.network_address)) - 1
            if prios[idx] is None or prio > prios[idx]:
                prios[idx] = prio
        return (starts, ends, collapsed, prios)

    def _table(self, version: int):
        tbl = self._tables[version]
        if tbl is None:
            tbl = self._build_version(version)
            self._tables[version] = tbl
        return tbl

    def build(self) -> None:
        """显式预构建查找表（不调用也会在首次查询时懒构建）。"""
        for version in (4, 6):
            self._table(version)

    def merged(self, version: Optional[int] = None) -> List[Network]:
        """返回合并后的段列表；version=4/6 时只返回对应版本。"""
        if version in (4, 6):
            return list(self._table(version)[2])
        return list(self._table(4)[2]) + list(self._table(6)[2])

    def lookup(self, addr: Union[str, Address, int]) -> LookupResult:
        """二分查找地址命中的段，返回 (段, 优先级)；未命中返回 hit=False。"""
        ip = parse_address(addr)
        starts, ends, networks, prios = self._table(ip.version)
        idx = bisect.bisect_right(starts, int(ip)) - 1
        if idx >= 0 and int(ip) <= ends[idx]:
            return LookupResult(True, networks[idx], prios[idx], ip.version)
        return LookupResult(False, version=ip.version)

    # 仅供基准测试对照：朴素线性扫描
    def lookup_linear(self, addr: Union[str, Address]) -> LookupResult:
        ip = parse_address(addr)
        best: Optional[Tuple[Network, int]] = None
        for net, prio in self._entries:
            if net.version == ip.version and ip in net:
                if best is None or prio > best[1]:
                    best = (net, prio)
        if best is None:
            return LookupResult(False, version=ip.version)
        return LookupResult(True, best[0], best[1], ip.version)
