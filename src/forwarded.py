"""Forwarded-chain parsing and trusted-proxy decision logic.

Only the Python 3 standard library is used (``ipaddress`` for IP/CIDR
validation and ``typing`` for annotations).

The header addressed here is the de-facto standard ``X-Forwarded-For``
(XFF): a comma separated chain where every participating proxy appends the
address it saw on its own inbound side::

    X-Forwarded-For: <client>, <proxy1>, <proxy2>, ...

Trust model
-----------
A connection is identified by its *direct* (peer) address -- the socket
remote address.  The XFF header is only consulted when that peer is a
configured trusted proxy.  When it is, the chain is walked **from right to
left**: every entry that is itself a trusted proxy is skipped, and the first
(untrusted) address reached is accepted as the real client.  Addresses to
its left cannot be verified and are never used -- this is what neutralises a
forged value injected by the client.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Union

__all__ = [
    "ChainEntry",
    "ParseReport",
    "Resolution",
    "TrustedProxies",
    "parse_chain",
    "resolve_client",
]

_IP = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
_NET = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

# Entry kinds
VALID = "valid"          # parsed as a real IP literal
UNKNOWN = "unknown"      # the literal token ``unknown`` (RFC 7239 placeholder)
EMPTY = "empty"          # blank item, e.g. "1.2.3.4,,5.6.7.8"
PORT = "port"            # looks like "1.2.3.4:8080" -- refused on purpose
INVALID = "invalid"      # any other unparseable token


@dataclass(frozen=True)
class ChainEntry:
    """One item of the X-Forwarded-For chain."""

    index: int
    raw: str
    kind: str
    ip: Optional[_IP] = None

    @property
    def usable(self) -> bool:
        """True when the entry is a normalised, valid IP literal."""
        return self.kind == VALID and self.ip is not None


@dataclass(frozen=True)
class ParseReport:
    """Structured result of :func:`parse_chain`."""

    entries: List[ChainEntry]
    errors: List[ChainEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every item was a valid IP literal (no placeholders)."""
        return not self.errors


@dataclass(frozen=True)
class Resolution:
    """Result of :func:`resolve_client`."""

    client_ip: str
    source: str                 # "direct" | "forwarded"
    trusted_hops: int
    header_used: bool
    reason: str
    report: Optional[ParseReport] = None

    def summary(self) -> str:
        via = "X-Forwarded-For" if self.header_used else "socket peer"
        return (
            f"client={self.client_ip} via={via} "
            f"trusted_hops={self.trusted_hops} ({self.reason})"
        )


class TrustedProxies:
    """Set of trusted proxy addresses / CIDR ranges."""

    def __init__(self, ranges: Iterable[Union[str, _NET]] = ()):
        self._ranges: List[_NET] = []
        for item in ranges:
            if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                self._ranges.append(item)
            else:
                self._ranges.append(ipaddress.ip_network(item, strict=False))

    def contains(self, address: Union[str, _IP]) -> bool:
        addr = address if isinstance(address, (ipaddress.IPv4Address,
                                               ipaddress.IPv6Address)) \
            else ipaddress.ip_address(address)
        return any(addr in network for network in self._ranges)


def parse_chain(header_value: Optional[str]) -> ParseReport:
    """Parse a raw ``X-Forwarded-For`` header value.

    Every item is classified independently; malformed items never raise and
    are returned in :attr:`ParseReport.errors` with their exact position, so
    callers can localise the bad item and decide what to trust.
    """

    entries: List[ChainEntry] = []
    errors: List[ChainEntry] = []

    if header_value is None:
        return ParseReport(entries, errors)

    for index, raw in enumerate(header_value.split(",")):
        token = raw.strip()
        entry = _classify(index, token)
        entries.append(entry)
        if not entry.usable:
            errors.append(entry)
    return ParseReport(entries, errors)


def _classify(index: int, token: str) -> ChainEntry:
    if token == "":
        return ChainEntry(index, token, EMPTY)
    if token.lower() == "unknown":
        return ChainEntry(index, token, UNKNOWN)

    # XFF does not carry ports. A token with a port is almost always a
    # misconfigured or hand-forged header -- reject instead of guessing.
    if _looks_like_port(token):
        return ChainEntry(index, token, PORT)

    try:
        return ChainEntry(index, token, VALID, ipaddress.ip_address(token))
    except ValueError:
        return ChainEntry(index, token, INVALID)


def _looks_like_port(token: str) -> bool:
    if token.startswith("["):                       # [2001:db8::1]:8080
        return "]:" in token
    if token.count(":") == 1 and "." in token:     # 192.0.2.1:8080
        return True
    return False


def resolve_client(
    direct_addr: str,
    header_value: Optional[str],
    trusted: TrustedProxies,
) -> Resolution:
    """Resolve the real client address.

    ``direct_addr`` is the socket peer address. ``header_value`` is the raw
    ``X-Forwarded-For`` header (or ``None`` when absent).

    Decision rules (right-to-left walk):

    1. Peer untrusted            -> header ignored entirely, use peer.
    2. Peer trusted, no header   -> use peer.
    3. First non-trusted address -> that address is the client.
    4. Every entry trusted       -> leftmost entry is the client.
    5. Bad / unknown item        -> stop the walk there; entries at or to its
       left are unverifiable; fall back to the peer when no usable client
       exists to its right.
    """

    peer = ipaddress.ip_address(direct_addr)
    report = parse_chain(header_value)

    # Rule 1: untrusted peer -- the header is attacker-controllable.
    if not trusted.contains(peer):
        return Resolution(
            client_ip=str(peer),
            source="direct",
            trusted_hops=0,
            header_used=False,
            reason="peer is not a trusted proxy; X-Forwarded-For ignored as "
                   "potentially forged",
            report=report,
        )

    # Rule 2: trusted peer but nothing to read.
    if not report.entries:
        return Resolution(
            client_ip=str(peer),
            source="direct",
            trusted_hops=1,
            header_used=False,
            reason="no X-Forwarded-For header supplied by trusted peer",
            report=report,
        )

    # Rules 3-5: walk the chain from the proxy closest to us.
    trusted_hops = 1
    for entry in reversed(report.entries):
        if not entry.usable:
            # Rule 5: unverifiable boundary. Everything left of it is
            # discarded; items already scanned were all trusted proxies.
            return Resolution(
                client_ip=str(peer),
                source="direct",
                trusted_hops=trusted_hops,
                header_used=False,
                reason=(
                    f"chain item #{entry.index} ({entry.raw!r}) is "
                    f"{entry.kind}; chain truncated there, entries at or to "
                    f"its left are unverifiable, falling back to peer"
                ),
                report=report,
            )
        assert entry.ip is not None
        if trusted.contains(entry.ip):
            trusted_hops += 1
            continue
        # Rule 3: first address that is not one of our proxies.
        return Resolution(
            client_ip=str(entry.ip),
            source="forwarded",
            trusted_hops=trusted_hops,
            header_used=True,
            reason=(
                f"first untrusted address scanning right-to-left at chain "
                f"item #{entry.index}; {entry.index} entries to its left "
                f"cannot be verified and are ignored"
            ),
            report=report,
        )

    # Rule 4: every entry belongs to a trusted proxy -- use the leftmost.
    leftmost = report.entries[0]
    return Resolution(
        client_ip=str(leftmost.ip),
        source="forwarded",
        trusted_hops=trusted_hops,
        header_used=True,
        reason="all chain entries are trusted proxies; using leftmost entry",
        report=report,
    )
