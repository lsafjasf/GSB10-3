"""SOCKS5 客户端握手：方法协商 + CONNECT 请求（仅标准库）。"""

from __future__ import annotations

import ipaddress
import socket

from .reply import (
    ATYP_DOMAIN,
    ATYP_IPV4,
    ATYP_IPV6,
    Reply,
    UnknownAddressType,
    parse_reply,
)

CMD_CONNECT = 0x01
METHOD_NO_AUTH = 0x00
METHOD_NO_ACCEPTABLE = 0xFF


class Socks5Error(Exception):
    """握手通用失败。"""


class NegotiationError(Socks5Error):
    """方法协商失败。"""


class ReplyError(Socks5Error):
    """收到非成功应答（rep != 0x00）。"""

    def __init__(self, reply: Reply):
        self.reply = reply
        super().__init__(reply.describe())


def _encode_address(host: str) -> tuple[int, bytes]:
    """把目标主机编码成 (ATYP, DST.ADDR 字段值)。

    域名字段带 1 字节长度前缀，长度取整个标签串的字节数（1..255）。
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        label = host.encode("idna")
        if not (1 <= len(label) <= 255):
            raise Socks5Error(
                f"domain name must be 1..255 bytes after IDNA, got {len(label)}"
            )
        return ATYP_DOMAIN, bytes([len(label)]) + label
    if isinstance(ip, ipaddress.IPv4Address):
        return ATYP_IPV4, ip.packed
    return ATYP_IPV6, ip.packed


def _recv_exactly(sock: socket.socket, count: int, field: str) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise Socks5Error(
                f"connection closed while reading {field!r}: "
                f"need {count} bytes, got {len(chunks)}"
            )
        chunks.extend(chunk)
    return bytes(chunks)


class Socks5Client:
    """在一条已连接到代理的 TCP socket 上完成 SOCKS5 握手。"""

    def __init__(self, sock: socket.socket):
        self._sock = sock

    def negotiate(self) -> None:
        self._sock.sendall(bytes([0x05, 0x01, METHOD_NO_AUTH]))
        response = _recv_exactly(self._sock, 2, "method response")
        ver, method = response[0], response[1]
        if ver != 0x05:
            raise NegotiationError(f"bad version in method response: 0x{ver:02x}")
        if method == METHOD_NO_ACCEPTABLE:
            raise NegotiationError("server rejected all authentication methods")
        if method != METHOD_NO_AUTH:
            raise NegotiationError(f"unsupported method selected: 0x{method:02x}")

    def read_reply(self) -> Reply:
        """按 ATYP 严格分长度读取并解析一条应答。"""
        header = _recv_exactly(self._sock, 4, "reply header")
        atyp = header[3]
        if atyp == ATYP_IPV4:
            addr = _recv_exactly(self._sock, 4, "bnd_addr(ipv4)")
        elif atyp == ATYP_IPV6:
            addr = _recv_exactly(self._sock, 16, "bnd_addr(ipv6)")
        elif atyp == ATYP_DOMAIN:
            length_octet = _recv_exactly(self._sock, 1, "domain length")
            addr = length_octet + _recv_exactly(
                self._sock, length_octet[0], "bnd_addr(domain)"
            )
        else:
            # 未知 ATYP：无法确定地址段长度，直接报错，绝不猜测着错位读。
            raise UnknownAddressType(atyp)
        port = _recv_exactly(self._sock, 2, "bnd_port")
        reply = parse_reply(header + addr + port)
        if not reply.ok:
            raise ReplyError(reply)
        return reply

    def connect(self, host: str, port: int) -> Reply:
        if not (0 <= port <= 0xFFFF):
            raise Socks5Error(f"port out of range: {port}")
        atyp, addr = _encode_address(host)
        request = (
            bytes([0x05, CMD_CONNECT, 0x00, atyp]) + addr + port.to_bytes(2, "big")
        )
        self._sock.sendall(request)
        return self.read_reply()

    def open(self, host: str, port: int) -> Reply:
        self.negotiate()
        return self.connect(host, port)
