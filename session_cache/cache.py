"""TLS-like short-lived connection session state cache.

Stdlib-only. All time access goes through an injectable clock.

Ticket wire format (binary, fields length-prefixed / fixed-width):

    MAGIC(4) | VER(1) | ISSUED(8, big-endian double) | EXPIRES(8)
    | PAYLOAD_LEN(4) | PAYLOAD(...) | HMAC_LEN(1) | HMAC(...)

PAYLOAD is a JSON object containing the opaque session state plus the
binding tuple (protocol_version, cipher_suite, sni). The HMAC tag
covers MAGIC..PAYLOAD with HMAC-SHA256 keyed by the server secret, so
any tampering with expiry, state or binding is detectable.

Eviction policy (in order, evaluated on every insert):
  1. If the same key already exists it is replaced/refreshed in place.
  2. Expired entries are lazily purged (also on lookup/touch).
  3. If still over capacity, the least-recently-used entry is evicted
    (recency updated on successful reuse, not on rejected lookups).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

MAGIC = b"SSC1"
TICKET_VERSION = 1
_HMAC_ALG = "sha256"
_TAG_LEN = hashlib.sha256().digest_size  # 32


class ReuseError(Exception):
    """Raised when a ticket cannot be reused. ``reason`` is machine-readable."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Binding:
    """Connection parameters the session is bound to."""

    protocol_version: str
    cipher_suite: str
    sni: str = ""

    def as_tuple(self):
        return (self.protocol_version, self.cipher_suite, self.sni)


@dataclass
class _Entry:
    state: dict
    binding: Binding
    issued_at: float
    expires_at: float
    estimated_bytes: int = 0


@dataclass
class CacheStats:
    issued: int = 0
    reused: int = 0
    rejected_expired: int = 0
    rejected_binding: int = 0
    rejected_forged: int = 0
    rejected_not_found: int = 0
    evicted_lru: int = 0
    lazy_expired_purged: int = 0

    @property
    def reuse_attempts(self) -> int:
        return (
            self.reused
            + self.rejected_expired
            + self.rejected_binding
            + self.rejected_forged
            + self.rejected_not_found
        )


class SessionTicketCache:
    """Bounded, TTL-based session cache with opaque signed tickets.

    Parameters
    ----------
    secret:
        Server-side HMAC key. Must stay stable across process restarts for
        tickets issued earlier to remain valid; rotate by issuing a new
        cache instance (old tickets then fail with FORGED).
    max_entries:
        Hard capacity upper bound.
    ttl_seconds:
        Default lifetime of a session entry.
    clock:
        Zero-argument callable returning current time in seconds. Inject
        ``FakeClock`` (or any callable) in tests.
    """

    def __init__(
        self,
        secret: bytes,
        max_entries: int = 1024,
        ttl_seconds: float = 300.0,
        clock=time.time,
    ):
        if not isinstance(secret, (bytes, bytearray)) or len(secret) < 16:
            raise ValueError("secret must be >=16 bytes")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._key = bytes(secret)
        self._max = max_entries
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._store: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.RLock()
        self.stats = CacheStats()

    # ------------------------------------------------------------------ keys

    @staticmethod
    def _ticket_id(entry: _Entry) -> str:
        digest = hashlib.sha256(
            json.dumps(
                [entry.issued_at, entry.expires_at, entry.binding.as_tuple(), entry.state],
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return digest[:32]

    # ------------------------------------------------------------- sign/code

    def _sign(self, blob: bytes) -> bytes:
        return hmac.new(self._key, blob, _HMAC_ALG).digest()

    def _encode_ticket(self, ticket_id: str, entry: _Entry) -> bytes:
        payload = json.dumps(
            {
                "id": ticket_id,
                "state": entry.state,
                "binding": {
                    "protocol_version": entry.binding.protocol_version,
                    "cipher_suite": entry.binding.cipher_suite,
                    "sni": entry.binding.sni,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        blob = b"".join(
            [
                MAGIC,
                bytes([TICKET_VERSION]),
                entry.issued_at.to_bytes(8, "big", signed=False) if False else _double(entry.issued_at),
                _double(entry.expires_at),
                len(payload).to_bytes(4, "big"),
                payload,
            ]
        )
        tag = self._sign(blob)
        return blob + bytes([len(tag)]) + tag

    def _decode_ticket(self, raw: bytes):
        """Return (ticket_id, state, binding, expires_at) or raise ReuseError."""
        try:
            if len(raw) < 4 + 1 + 8 + 8 + 4 + 1 + _TAG_LEN:
                raise ValueError("too short")
            off = 0
            if raw[:4] != MAGIC:
                raise ValueError("bad magic")
            off = 4
            if raw[off] != TICKET_VERSION:
                raise ValueError("bad ticket version")
            off += 1
            issued_at = _undouble(raw[off:off + 8]); off += 8
            expires_at = _undouble(raw[off:off + 8]); off += 8
            plen = int.from_bytes(raw[off:off + 4], "big"); off += 4
            payload = raw[off:off + plen]; off += plen
            tlen = raw[off]; off += 1
            tag = raw[off:off + tlen]
            if off + tlen != len(raw):
                raise ValueError("trailing garbage")
        except (IndexError, ValueError, OverflowError, OSError) as exc:
            raise ReuseError("FORGED", f"malformed ticket: {exc}")

        expected = self._sign(raw[: off - 1])
        if tlen != _TAG_LEN or not hmac.compare_digest(tag, expected):
            raise ReuseError("FORGED", "HMAC verification failed")

        try:
            data = json.loads(payload.decode())
            binding = Binding(
                protocol_version=data["binding"]["protocol_version"],
                cipher_suite=data["binding"]["cipher_suite"],
                sni=data["binding"].get("sni", ""),
            )
            return data["id"], data["state"], binding, expires_at
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ReuseError("FORGED", f"undecodable payload: {exc}")

    # --------------------------------------------------------------- public

    def issue(self, state: dict, binding: Binding, ttl_seconds: float | None = None) -> bytes:
        """Store ``state`` bound to ``binding`` and return an opaque ticket."""
        now = float(self._clock())
        ttl = self._ttl if ttl_seconds is None else float(ttl_seconds)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        entry = _Entry(
            state=state,
            binding=binding,
            issued_at=now,
            expires_at=now + ttl,
        )
        ticket_id = self._ticket_id(entry)
        ticket = self._encode_ticket(ticket_id, entry)
        entry.estimated_bytes = len(ticket) + _ENTRY_OVERHEAD + _dict_size(state)

        with self._lock:
            if ticket_id in self._store:
                # Extremely unlikely collision of fresh timestamps; treat as refresh.
                self._store.pop(ticket_id)
            self._purge_expired_locked(now)
            while len(self._store) >= self._max:
                self._store.popitem(last=False)
                self.stats.evicted_lru += 1
            self._store[ticket_id] = entry
            self.stats.issued += 1
        return ticket

    def resume(self, ticket: bytes, binding: Binding):
        """Validate and redeem a ticket.

        Returns the stored ``state`` dict on success. Raises ``ReuseError``
        with reason in:
          FORGED  - malformed or bad HMAC (tampered / foreign secret)
          EXPIRED - signature valid but lifetime elapsed
          BINDING_MISMATCH - protocol_version or cipher_suite (or sni) differs
          NOT_FOUND - valid ticket but the server already evicted the entry
        """
        if not isinstance(ticket, (bytes, bytearray)):
            self.stats.rejected_forged += 1
            raise ReuseError("FORGED", "ticket is not bytes")

        # Authenticity is verified before anything else.
        try:
            ticket_id, state, ticket_binding, expires_at = self._decode_ticket(bytes(ticket))
        except ReuseError as exc:
            if exc.reason == "FORGED":
                self.stats.rejected_forged += 1
            raise

        now = float(self._clock())
        with self._lock:
            if expires_at <= now:
                self._store.pop(ticket_id, None)
                self.stats.rejected_expired += 1
                raise ReuseError(
                    "EXPIRED",
                    f"expired at {expires_at:.3f}, now {now:.3f}",
                )

            entry = self._store.get(ticket_id)
            if entry is None:
                # Could have been LRU-evicted or purged while still valid.
                self.stats.rejected_not_found += 1
                raise ReuseError("NOT_FOUND", "server no longer holds this session")

            if entry.binding.as_tuple() != ticket_binding.as_tuple():
                self.stats.rejected_forged += 1
                raise ReuseError("FORGED", "ticket binding disagrees with store")

            if binding.as_tuple() != entry.binding.as_tuple():
                self.stats.rejected_binding += 1
                wanted = binding.as_tuple()
                got = entry.binding.as_tuple()
                fields = ("protocol_version", "cipher_suite", "sni")
                diff = [f for f, a, b in zip(fields, wanted, got) if a != b]
                raise ReuseError(
                    "BINDING_MISMATCH",
                    f"client {dict(zip(fields, wanted))} vs session "
                    f"{dict(zip(fields, got))}; changed: {','.join(diff)}",
                )

            self._store.move_to_end(ticket_id, last=True)
            self.stats.reused += 1
            return dict(entry.state)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_expired_locked(float(self._clock()))

    def _purge_expired_locked(self, now: float) -> int:
        dead = [k for k, e in self._store.items() if e.expires_at <= now]
        for k in dead:
            self._store.pop(k, None)
        self.stats.lazy_expired_purged += len(dead)
        return len(dead)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def memory_bytes(self) -> int:
        """Rough retained memory: ticket-equivalent bytes + per-entry overhead."""
        with self._lock:
            return sum(e.estimated_bytes for e in self._store.values())

    def reset_stats(self) -> None:
        with self._lock:
            self.stats = CacheStats()


# --------------------------------------------------------------------- helpers

def _double(x: float) -> bytes:
    import struct
    return struct.pack(">d", x)


def _undouble(b: bytes) -> float:
    import struct
    return struct.unpack(">d", b)[0]


def _dict_size(d: dict) -> int:
    try:
        return len(json.dumps(d, separators=(",", ":"), sort_keys=True, default=str))
    except (TypeError, ValueError):
        return 64


_ENTRY_OVERHEAD = 128  # OrderedDict node + _Entry/Binding python object estimate
