"""Minimal SOCKS5 client handshake library (Python 3 standard library only).

Wire format recap (RFC 1928):

  Greeting:        VER(1) NMETHODS(1) METHODS(NMETHODS)
  Method reply:    VER(1) METHOD(1)            METHOD=0xFF -> no acceptable methods
  Request:         VER(1) CMD(1) RSV(1) ATYP(1) ADDR(var) PORT(2, big-endian)
  Reply:           VER(1) REP(1) RSV(1) ATYP(1) BND.ADDR(var) BND.PORT(2)

The variable-length ADDR field is the classic parsing trap: its length is
derived differently per ATYP, and misreading it desynchronises every field
that follows (notably PORT):

  ATYP=0x01 (IPv4):   ADDR is exactly 4 raw octets, no length prefix.
  ATYP=0x03 (DOMAIN): ADDR is LEN(1 byte) followed by LEN octets; LEN must
                      be in 1..255 (0 is malformed, there is no 16-bit length).
  ATYP=0x04 (IPv6):   ADDR is exactly 16 raw octets, no length prefix.

PORT is always the final 2 bytes, big-endian.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

SOCKS_VERSION = 0x05

CMD_CONNECT = 0x01

METHOD_NO_AUTH = 0x00
METHOD_GSSAPI = 0x01
METHOD_USER_PASS = 0x02
METHOD_NO_ACCEPTABLE = 0xFF

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

ATYP_NAMES = {ATYP_IPV4: "IPv4", ATYP_DOMAIN: "DOMAIN", ATYP_IPV6: "IPv6"}

# REP -> human readable result. Only 0x00 is success; codes outside
# 0x00..0x08 are unassigned by RFC 1928 and must NOT be treated as success.
REPLY_MESSAGES = {
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


def describe_reply(rep: int) -> str:
    """Map a REP code to a readable result; unknown codes stay unknown."""
    if rep in REPLY_MESSAGES:
        return REPLY_MESSAGES[rep]
    return "unassigned reply code 0x%02x (treated as failure)" % rep


class Socks5Error(Exception):
    """Base class for all errors raised by this module."""


class TruncatedReply(Socks5Error):
    """The reply ended before all expected bytes were present."""


class MalformedReply(Socks5Error):
    """The reply bytes violate the protocol (bad VER/RSV/ATYP/length...)."""


@dataclass
class Reply:
    """A fully parsed SOCKS5 reply."""

    ver: int
    rep: int
    rsv: int
    atyp: int
    addr_raw: bytes   # address octets exactly as they appeared on the wire
    addr: str         # printable form (dotted IPv4 / domain / compressed IPv6)
    port: int
    raw: bytes        # the complete reply as received

    @property
    def ok(self) -> bool:
        # Success is defined strictly as REP == 0x00. Unknown codes are not ok.
        return self.rep == 0x00

    @property
    def result(self) -> str:
        return describe_reply(self.rep)

    @property
    def atyp_name(self) -> str:
        return ATYP_NAMES.get(self.atyp, "0x%02x" % self.atyp)


def parse_reply(data: bytes) -> Reply:
    """Parse one complete SOCKS5 reply buffer, strictly.

    Raises TruncatedReply if bytes are missing, MalformedReply if the bytes
    are structurally invalid (including trailing garbage after PORT).
    """
    if len(data) < 4:
        raise TruncatedReply(
            "reply header needs 4 bytes, got %d" % len(data))
    ver, rep, rsv, atyp = data[0], data[1], data[2], data[3]
    if ver != SOCKS_VERSION:
        raise MalformedReply("VER=0x%02x, expected 0x05" % ver)
    if rsv != 0x00:
        raise MalformedReply("RSV=0x%02x, expected 0x00" % rsv)

    # --- variable-length address: length rule depends on ATYP ---
    if atyp == ATYP_IPV4:
        addr_off, addr_len = 4, 4
    elif atyp == ATYP_IPV6:
        addr_off, addr_len = 4, 16
    elif atyp == ATYP_DOMAIN:
        if len(data) < 5:
            raise TruncatedReply("missing domain length byte")
        addr_len = data[4]  # single length octet, NOT a 16-bit field
        if addr_len == 0:
            raise MalformedReply("domain length must be 1..255, got 0")
        addr_off = 5
    else:
        raise MalformedReply("unknown ATYP=0x%02x" % atyp)

    total = addr_off + addr_len + 2  # +2 for the big-endian PORT
    if len(data) < total:
        raise TruncatedReply(
            "reply needs %d bytes for ATYP=%s, got %d"
            % (total, ATYP_NAMES.get(atyp, "0x%02x" % atyp), len(data)))
    if len(data) > total:
        raise MalformedReply(
            "%d trailing byte(s) after PORT" % (len(data) - total))

    addr_raw = bytes(data[addr_off:addr_off + addr_len])
    port = int.from_bytes(data[total - 2:total], "big")

    if atyp == ATYP_IPV4:
        addr = socket.inet_ntop(socket.AF_INET, addr_raw)
    elif atyp == ATYP_IPV6:
        addr = socket.inet_ntop(socket.AF_INET6, addr_raw)
    else:
        addr = addr_raw.decode("utf-8", "replace")

    return Reply(ver=ver, rep=rep, rsv=rsv, atyp=atyp,
                 addr_raw=addr_raw, addr=addr, port=port, raw=bytes(data))


def encode_address(host: str) -> bytes:
    """Encode a target host as ATYP + ADDR for a SOCKS5 request."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if not 1 <= len(encoded) <= 255:
            raise ValueError(
                "domain name must encode to 1..255 bytes, got %d" % len(encoded))
        return bytes([ATYP_DOMAIN, len(encoded)]) + encoded
    if ip.version == 4:
        return bytes([ATYP_IPV4]) + ip.packed
    return bytes([ATYP_IPV6]) + ip.packed


def _recv_exactly(sock: socket.socket, n: int, what: str) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise TruncatedReply(
                "connection closed while reading %s (%d/%d bytes)"
                % (what, len(buf), n))
        buf += chunk
    return bytes(buf)


def read_reply(sock: socket.socket) -> Reply:
    """Read one reply from a socket, then validate it with parse_reply.

    The amount to read is driven by ATYP exactly as documented in parse_reply,
    so a hostile or buggy server cannot make us mis-frame the PORT field.
    """
    header = _recv_exactly(sock, 4, "reply header")
    atyp = header[3]
    if atyp == ATYP_IPV4:
        rest = _recv_exactly(sock, 4 + 2, "IPv4 BND.ADDR+BND.PORT")
    elif atyp == ATYP_IPV6:
        rest = _recv_exactly(sock, 16 + 2, "IPv6 BND.ADDR+BND.PORT")
    elif atyp == ATYP_DOMAIN:
        length_byte = _recv_exactly(sock, 1, "domain length")
        n = length_byte[0]
        if n == 0:
            raise MalformedReply("domain length must be 1..255, got 0")
        rest = length_byte + _recv_exactly(sock, n + 2, "domain BND.ADDR+BND.PORT")
    else:
        raise MalformedReply("unknown ATYP=0x%02x" % atyp)
    return parse_reply(header + rest)


def socks5_connect(sock: socket.socket, host: str, port: int,
                   methods=(METHOD_NO_AUTH,)) -> Reply:
    """Run the full client handshake on an already-connected socket.

    Negotiates an auth method, sends CONNECT for (host, port) and returns the
    parsed Reply. Protocol violations raise Socks5Error subclasses; a
    non-zero REP does NOT raise -- inspect reply.ok / reply.result instead.
    """
    if not 0 <= port <= 65535:
        raise ValueError("port out of range: %d" % port)
    if not methods:
        raise ValueError("at least one auth method is required")

    # --- method negotiation ---
    sock.sendall(bytes([SOCKS_VERSION, len(methods), *methods]))
    ver, method = _recv_exactly(sock, 2, "method selection")
    if ver != SOCKS_VERSION:
        raise MalformedReply("method reply VER=0x%02x, expected 0x05" % ver)
    if method == METHOD_NO_ACCEPTABLE:
        raise Socks5Error("server accepts none of the offered methods")
    if method not in methods:
        raise MalformedReply(
            "server selected method 0x%02x we never offered" % method)
    if method != METHOD_NO_AUTH:
        # Only no-auth is implemented; fail loudly instead of desynchronising.
        raise Socks5Error("auth method 0x%02x not implemented" % method)

    # --- CONNECT request ---
    request = (bytes([SOCKS_VERSION, CMD_CONNECT, 0x00])
               + encode_address(host)
               + port.to_bytes(2, "big"))
    sock.sendall(request)
    return read_reply(sock)
