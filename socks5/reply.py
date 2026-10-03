"""SOCKS5 服务器应答（RFC 1928 第 6 节）的严格解析器。

应答报文布局::

    +----+-----+-------+------+----------+----------+
    |VER | REP |  RSV  | ATYP | BND.ADDR | BND.PORT |
    +----+-----+-------+------+----------+----------+
    | 1  |  1  | X'00' |  1   | Variable |    2     |
    +----+-----+-------+------+----------+----------+

长度字段读法（关键，读错会导致后续字段全部错位）：

* ATYP = 0x01 (IPv4)  : BND.ADDR 定长 4 字节，无长度前缀。
* ATYP = 0x03 (DOMAIN): BND.ADDR 第 1 个字节是长度 N（1..255），
                        随后紧跟 N 字节域名；总长度 = 1 + N。
* ATYP = 0x04 (IPv6)  : BND.ADDR 定长 16 字节，无长度前缀。
* BND.PORT 固定 2 字节，大端序，紧跟在 BND.ADDR 之后。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

VER_SOCKS5 = 0x05

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

#: REP 应答码 -> 可读描述。不在表内的码一律视为“未识别”，绝不当成功。
REP_MESSAGES = {
    0x00: "succeeded",
    0x01: "general SOCKS server failure",
    0x02: "connection not allowed by ruleset",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}

ATYP_NAMES = {
    ATYP_IPV4: "IPV4",
    ATYP_DOMAIN: "DOMAIN",
    ATYP_IPV6: "IPV6",
}


class ReplyParseError(Exception):
    """应答解析失败的基类。"""


class TruncatedReply(ReplyParseError):
    """报文被截断：声明的长度超出实际收到的字节数。"""

    def __init__(self, field: str, need: int, got: int):
        self.field = field
        self.need = need
        self.got = got
        super().__init__(
            f"truncated reply: field {field!r} needs {need} byte(s), only {got} available"
        )


class UnknownReplyCode(ReplyParseError):
    """REP 不在已知映射表内。未知码不能按成功处理。"""

    def __init__(self, rep: int):
        self.rep = rep
        super().__init__(f"unknown reply code: 0x{rep:02x}")


class UnknownAddressType(ReplyParseError):
    """ATYP 不是 0x01/0x03/0x04，无法确定 BND.ADDR 长度。"""

    def __init__(self, atyp: int):
        self.atyp = atyp
        super().__init__(f"unknown address type: 0x{atyp:02x}")


class BadVersion(ReplyParseError):
    """VER 不是 0x05。"""

    def __init__(self, ver: int):
        self.ver = ver
        super().__init__(f"bad socks version: 0x{ver:02x}")


@dataclass(frozen=True)
class Reply:
    """解析后的应答。rep != 0x00 时 ok 为 False。"""

    ver: int
    rep: int
    rsv: int
    atyp: int
    bnd_addr: str
    bnd_port: int

    @property
    def ok(self) -> bool:
        return self.rep == 0x00

    @property
    def rep_message(self) -> str:
        return REP_MESSAGES[self.rep]

    @property
    def atyp_name(self) -> str:
        return ATYP_NAMES[self.atyp]

    def describe(self) -> str:
        status = "OK" if self.ok else "FAIL"
        return (
            f"[{status}] rep=0x{self.rep:02x} ({self.rep_message}) "
            f"bnd={self.bnd_addr}:{self.bnd_port} atyp={self.atyp_name}"
        )


def _need(data: bytes, offset: int, count: int, field: str) -> None:
    if len(data) - offset < count:
        raise TruncatedReply(field, count, max(0, len(data) - offset))


def parse_reply(data: bytes) -> Reply:
    """严格解析一条 SOCKS5 应答。

    成功返回 :class:`Reply`；结构非法或码未识别时抛出
    :class:`ReplyParseError` 的子类。未知 REP 抛
    :class:`UnknownReplyCode`，绝不静默当作成功。
    """
    _need(data, 0, 4, "header")
    ver, rep, rsv, atyp = data[0], data[1], data[2], data[3]

    if ver != VER_SOCKS5:
        raise BadVersion(ver)
    if rep not in REP_MESSAGES:
        raise UnknownReplyCode(rep)

    if atyp == ATYP_IPV4:
        _need(data, 4, 4, "bnd_addr(ipv4)")
        addr = str(ipaddress.IPv4Address(data[4:8]))
        port_off = 8
    elif atyp == ATYP_DOMAIN:
        _need(data, 4, 1, "bnd_addr(domain length)")
        n = data[4]
        _need(data, 5, n, "bnd_addr(domain)")
        addr = data[5 : 5 + n].decode("utf-8", errors="replace")
        port_off = 5 + n
    elif atyp == ATYP_IPV6:
        _need(data, 4, 16, "bnd_addr(ipv6)")
        addr = str(ipaddress.IPv6Address(data[4:20]))
        port_off = 20
    else:
        raise UnknownAddressType(atyp)

    _need(data, port_off, 2, "bnd_port")
    port = int.from_bytes(data[port_off : port_off + 2], "big")

    return Reply(ver=ver, rep=rep, rsv=rsv, atyp=atyp, bnd_addr=addr, bnd_port=port)
