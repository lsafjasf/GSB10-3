"""Hand-written, byte-by-byte reference parser for SOCKS5 replies.

This is the independent "second implementation" used to cross-check
socks5.parse_reply field by field. It deliberately shares no code with
the library: it walks the buffer with an explicit cursor, copies bytes
one at a time, and computes the port by hand. Display formatting of the
address (inet_ntop / utf-8 decode) is normalised the same way as the
library so the diff script can compare the printable form too.
"""

from __future__ import annotations

import socket


class RefError(Exception):
    """Reference parser failure; `kind` is 'truncated' or 'malformed'."""

    def __init__(self, kind: str, detail: str):
        super().__init__("%s: %s" % (kind, detail))
        self.kind = kind
        self.detail = detail


def reference_parse(data: bytes) -> dict:
    pos = 0

    def take(n, what):
        nonlocal pos
        if pos + n > len(data):
            raise RefError(
                "truncated",
                "need %d byte(s) for %s at offset %d, only %d left"
                % (n, what, pos, len(data) - pos))
        out = bytearray()
        for i in range(n):            # copy byte by byte on purpose
            out.append(data[pos + i])
        pos += n
        return bytes(out)

    ver = take(1, "VER")[0]
    rep = take(1, "REP")[0]
    rsv = take(1, "RSV")[0]
    atyp = take(1, "ATYP")[0]

    if ver != 0x05:
        raise RefError("malformed", "VER is 0x%02x, not 0x05" % ver)
    if rsv != 0x00:
        raise RefError("malformed", "RSV is 0x%02x, not 0x00" % rsv)

    if atyp == 0x01:                       # IPv4: fixed 4 octets
        addr_raw = take(4, "IPv4 address")
        addr = socket.inet_ntop(socket.AF_INET, addr_raw)
    elif atyp == 0x04:                     # IPv6: fixed 16 octets
        addr_raw = take(16, "IPv6 address")
        addr = socket.inet_ntop(socket.AF_INET6, addr_raw)
    elif atyp == 0x03:                     # domain: 1 length octet + N octets
        n = take(1, "domain length")[0]
        if n == 0:
            raise RefError("malformed", "domain length 0 is not allowed")
        addr_raw = take(n, "domain name")
        addr = addr_raw.decode("utf-8", "replace")
    else:
        raise RefError("malformed", "ATYP 0x%02x is not a known address type" % atyp)

    hi = take(1, "port high byte")[0]
    lo = take(1, "port low byte")[0]
    port = hi * 256 + lo                   # big-endian, computed by hand

    if pos != len(data):
        raise RefError("malformed",
                       "%d extra byte(s) after end of reply" % (len(data) - pos))

    return {
        "ver": ver,
        "rep": rep,
        "rsv": rsv,
        "atyp": atyp,
        "addr_raw": addr_raw,
        "addr": addr,
        "port": port,
    }
