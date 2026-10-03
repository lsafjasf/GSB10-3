"""来源头部解析与信任判定。

仅依赖标准库。支持两种附加头部：

* ``X-Forwarded-For``：逗号分隔的 IP 列表，按"左旧右新"排列；
* RFC 7239 的 ``Forwarded``：逗号分隔的转发元素，取其中的 ``for=`` 地址。

信任模型：只有当直连对端（TCP 对端）位于可信代理网段内时才采纳头部；
随后从链的右端（最靠近本机的一跳）向左扫描，跳过可信代理，
第一个"不可信地址"即判定为真实客户端。链中非法项左侧的一切内容都视为
不可信（伪造内容本就只能从左侧注入）。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Iterable, Optional, Union

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


@dataclass(frozen=True)
class ChainEntry:
    """地址链中的一项。

    index 从 0 开始，方向为头部书写顺序（最左端 index=0，最早的一跳）。
    ip 为 None 表示该项无法解析，error 给出原因。
    """

    index: int
    raw: str
    ip: Optional[IPAddress] = None
    error: Optional[str] = None

    @property
    def valid(self) -> bool:
        return self.ip is not None


@dataclass(frozen=True)
class ParseResult:
    header: str
    entries: tuple[ChainEntry, ...]
    kind: str  # "xff" 或 "forwarded"

    @property
    def errors(self) -> tuple[ChainEntry, ...]:
        return tuple(e for e in self.entries if e.error is not None)

    @property
    def valid_ips(self) -> tuple[IPAddress, ...]:
        return tuple(e.ip for e in self.entries if e.ip is not None)


@dataclass(frozen=True)
class Resolution:
    """最终判定结果。

    client_ip      采用的客户端地址
    source         direct        直连对端不可信，头部整体被忽略
                   header        从头部链中解析出真实客户端
                   header-prefix 头部链全部可信，只能采用最左端（可被伪造）
    direct_ip      直连对端地址
    trusted        直连对端是否可信
    hops           被采纳、跳过的可信代理地址
    chain          头部解析结果（无头部时为 None）
    blocked_index  扫描在链中被截断的位置（非法项 index），无则 None
    reason         取舍理由（人读）
    spoofable      结果是否可能被外部伪造
    """

    client_ip: IPAddress
    source: str
    direct_ip: IPAddress
    trusted: bool
    hops: tuple[IPAddress, ...] = ()
    chain: Optional[ParseResult] = None
    blocked_index: Optional[int] = None
    reason: str = ""
    spoofable: bool = False


# ---------- 解析 ----------

def _split_csv(header: str) -> tuple[str, ...]:
    # Forwarded 的引号字符串中可能含逗号，简单状态机切分。
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in header:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    last = "".join(buf).strip()
    if last or parts:
        parts.append(last)
    return tuple(p for p in parts if p != "")


def _parse_ip_token(token: str) -> tuple[Optional[IPAddress], Optional[str]]:
    """解析单个地址记号，允许可选端口（IPv4 用 a.b.c.d:port，IPv6 用 [v6]:port）。"""

    token = token.strip()
    if not token:
        return None, "empty address"
    if token in ("unknown",):
        return None, "RFC 7239 'unknown' 标识符不是具体地址"
    if token.startswith("_"):
        return None, "混淆标识符 %r 无法映射到地址" % token
    try:
        if token.startswith("["):  # [IPv6] 或 [IPv6]:port
            end = token.find("]")
            if end == -1:
                return None, "IPv6 方括号未闭合: %r" % token
            host = token[1:end]
            tail = token[end + 1:]
            if tail and not (tail.startswith(":") and tail[1:].isdigit()):
                return None, "端口部分非法: %r" % token
            return ipaddress.ip_address(host), None
        if token.count(":") == 1 and token.rsplit(":", 1)[1].isdigit():
            host = token.rsplit(":", 1)[0]
            return ipaddress.ip_address(host), None
        return ipaddress.ip_address(token), None
    except ValueError:
        return None, "不是合法的 IP 地址: %r" % token


def parse_x_forwarded_for(header: str) -> ParseResult:
    """解析 X-Forwarded-For（含 X-Real-IP 等逗号分隔地址头部）。"""

    entries: list[ChainEntry] = []
    for i, raw in enumerate(_split_csv(header)):
        ip, err = _parse_ip_token(raw)
        entries.append(ChainEntry(index=i, raw=raw, ip=ip, error=err))
    return ParseResult(header=header, entries=tuple(entries), kind="xff")


def parse_forwarded(header: str) -> ParseResult:
    """解析 RFC 7239 Forwarded 头部，提取每项的 for= 参数。"""

    entries: list[ChainEntry] = []
    for i, raw in enumerate(_split_csv(header)):
        for_param = None
        malformed = None
        for param in raw.split(";"):
            name, sep, value = param.strip().partition("=")
            if not sep:
                continue
            if name.strip().lower() == "for":
                for_param = value.strip()
        if for_param is None:
            malformed = "元素缺少 for= 参数: %r" % raw
            entries.append(ChainEntry(index=i, raw=raw, error=malformed))
            continue
        token = for_param
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
            token = token[1:-1]
        ip, err = _parse_ip_token(token)
        entries.append(ChainEntry(index=i, raw=raw, ip=ip, error=err))
    return ParseResult(header=header, entries=tuple(entries), kind="forwarded")


# ---------- 信任判定 ----------

def _build_networks(
    trusted_proxies: Iterable[Union[str, IPNetwork]],
) -> tuple[IPNetwork, ...]:
    nets: list[IPNetwork] = []
    for item in trusted_proxies:
        if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            nets.append(item)
        else:
            nets.append(ipaddress.ip_network(item, strict=False))
    return tuple(nets)


def _is_trusted(ip: IPAddress, networks: Iterable[IPNetwork]) -> bool:
    return any(ip in net for net in networks)


def resolve_client(
    direct_addr: Union[str, IPAddress],
    trusted_proxies: Iterable[Union[str, IPNetwork]],
    *,
    x_forwarded_for: Optional[str] = None,
    forwarded: Optional[str] = None,
) -> Resolution:
    """根据直连地址与附加头部判定真实客户端地址。

    若同时提供两种头部，优先采用 X-Forwarded-For，Forwarded 作为
    交叉参考不参与判定（避免双头注入带来的歧义）。
    """

    direct = ipaddress.ip_address(str(direct_addr))
    networks = _build_networks(trusted_proxies)

    if not _is_trusted(direct, networks):
        return Resolution(
            client_ip=direct,
            source="direct",
            direct_ip=direct,
            trusted=False,
            reason="直连对端 %s 不在可信代理范围内，附加头部一律忽略（可能被外部伪造）"
            % direct,
            spoofable=False,
        )

    if x_forwarded_for is not None:
        chain = parse_x_forwarded_for(x_forwarded_for)
    elif forwarded is not None:
        chain = parse_forwarded(forwarded)
    else:
        return Resolution(
            client_ip=direct,
            source="direct",
            direct_ip=direct,
            trusted=True,
            reason="对端 %s 可信但请求未携带来源头部，直连地址即客户端" % direct,
        )

    # 从右向左扫描：右端是最靠近本机的一跳，由可信代理写入，可采信。
    hops: list[IPAddress] = []
    blocked_index: Optional[int] = None
    for entry in reversed(chain.entries):
        if entry.error is not None:
            blocked_index = entry.index
            break  # 该位置及其左侧全部视为伪造/噪声，停止采信
        assert entry.ip is not None
        if _is_trusted(entry.ip, networks):
            hops.append(entry.ip)
            continue
        hops.reverse()
        return Resolution(
            client_ip=entry.ip,
            source="header",
            direct_ip=direct,
            trusted=True,
            hops=tuple(hops),
            chain=chain,
            reason="链位置 %d 的 %s 是首个不可信地址；其右侧 %d 跳均为可信代理"
            % (entry.index, entry.ip, len(hops)),
            spoofable=False,
        )

    # 没有找到任何不可信地址：链为空、全是可信代理，或被非法项截断。
    valid = chain.valid_ips
    if blocked_index is not None and not hops:
        return Resolution(
            client_ip=direct,
            source="direct",
            direct_ip=direct,
            trusted=True,
            hops=(),
            chain=chain,
            blocked_index=blocked_index,
            reason="链位置 %d 出现非法项且其右侧无可用地址，回退直连地址 %s"
            % (blocked_index, direct),
        )
    if valid:
        # 全链可信：只能假定最左端是客户端；该值可被任一跳伪造，标记风险。
        client = valid[0]
        hops.reverse()
        reason = "链中全部为可信代理，采用最左端地址 %s；该值理论上可被代理伪造" % client
        if blocked_index is not None:
            reason = (
                "链位置 %d 出现非法项，其右侧全部为可信代理；"
                "采用非法项左侧最近的合法地址 %s，可信度下降" % (blocked_index, client)
            )
        return Resolution(
            client_ip=client,
            source="header-prefix",
            direct_ip=direct,
            trusted=True,
            hops=tuple(hops),
            chain=chain,
            blocked_index=blocked_index,
            reason=reason,
            spoofable=True,
        )
    return Resolution(
        client_ip=direct,
        source="direct",
        direct_ip=direct,
        trusted=True,
        chain=chain,
        blocked_index=blocked_index,
        reason="头部不含任何合法地址%s，回退直连地址 %s"
        % ("（非法位置 %d）" % blocked_index if blocked_index is not None else "", direct),
    )
