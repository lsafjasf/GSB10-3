"""Bounded session-state cache for short-lived (e.g. TLS-like) connections.

Guarantees
----------
* **Capacity bound** -- at most ``max_entries`` live states are retained.
* **TTL** -- every entry has an explicit ``expires_at``; stale entries are
  removed lazily (on every resume/store and via :meth:`purge_expired`).
* **Eviction** -- strict LRU on the *access* order (store + successful resume
  both count as an access). Expired entries are reaped before an LRU victim is
  selected, so capacity is never spent on dead state.
* **Binding check** -- a resume only succeeds when the offered protocol
  version AND cipher suite match the values bound into the issued ticket.
* **Tamper detection** -- tickets carry an HMAC (see :mod:`ticket`); a bad
  signature is rejected before any cache lookup happens.

Time is injectable via the ``clock`` parameter (defaults to ``time.time``).
"""

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .ticket import TicketError, issue_ticket, verify_ticket


class ResumeStatus(str, Enum):
    REUSED = "reused"
    REJECTED = "rejected"


class RejectReason(str, Enum):
    MALFORMED = "malformed_ticket"
    FORGED = "forged_ticket"
    EXPIRED_TICKET = "expired_ticket"
    UNKNOWN_SESSION = "unknown_session"
    PROTOCOL_MISMATCH = "protocol_version_mismatch"
    CIPHER_MISMATCH = "cipher_suite_mismatch"
    EXPIRED_IN_CACHE = "expired_in_cache"
    EVICTED = "session_evicted"


@dataclass(frozen=True)
class SessionState:
    session_id: str
    protocol_version: str
    cipher_suite: str
    issued_at: float
    expires_at: float
    secret: bytes = b""

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass(frozen=True)
class ResumeResult:
    status: ResumeStatus
    reason: Optional[RejectReason]
    detail: str
    state: Optional[SessionState] = None

    @property
    def reused(self) -> bool:
        return self.status is ResumeStatus.REUSED


@dataclass
class CacheStats:
    issued: int = 0
    reused: int = 0
    rejected: int = 0
    forged_or_malformed: int = 0
    evictions: int = 0
    lazy_expiries: int = 0
    by_reason: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            "issued": self.issued,
            "reused": self.reused,
            "rejected": self.rejected,
            "forged_or_malformed": self.forged_or_malformed,
            "evictions": self.evictions,
            "lazy_expiries": self.lazy_expiries,
            "by_reason": dict(self.by_reason),
        }


class SessionCache:
    """Thread-safe, capacity- and time-bounded session cache."""

    def __init__(
        self,
        server_key: bytes,
        max_entries: int = 1024,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not isinstance(server_key, (bytes, bytearray)) \
                or len(server_key) < 16:
            raise ValueError("server_key must be at least 16 random bytes")
        self._key = bytes(server_key)
        self._max_entries = max_entries
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._store: "OrderedDict[str, SessionState]" = OrderedDict()
        self._evicted_ids: "deque[str]" = deque(maxlen=max_entries)
        self._stats = CacheStats()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ core

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def stats(self) -> CacheStats:
        return self._stats

    def live_size(self) -> int:
        """Number of currently cached entries (expired ones reaped first)."""
        with self._lock:
            self.purge_expired()
            return len(self._store)

    def purge_expired(self) -> int:
        """Lazy cleanup: drop every entry whose TTL has elapsed.

        Safe to call any time; resume()/store() call it automatically.
        Returns how many entries were removed.
        """
        now = self._clock()
        dead = [sid for sid, st in self._store.items() if st.is_expired(now)]
        for sid in dead:
            del self._store[sid]
        self._stats.lazy_expiries += len(dead)
        return len(dead)

    def store(self, session_id: str, protocol_version: str,
              cipher_suite: str, secret: bytes = b"") -> str:
        """Create a session state, cache it and return its opaque ticket."""
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not protocol_version or not cipher_suite:
            raise ValueError("protocol_version and cipher_suite are required")
        with self._lock:
            now = self._clock()
            state = SessionState(
                session_id=session_id,
                protocol_version=protocol_version,
                cipher_suite=cipher_suite,
                issued_at=now,
                expires_at=now + self._ttl,
                secret=bytes(secret),
            )

            self.purge_expired()

            if session_id in self._store:
                self._store.move_to_end(session_id)
                self._store[session_id] = state
            else:
                while len(self._store) >= self._max_entries:
                    victim_id, _ = self._store.popitem(last=False)  # LRU
                    self._evicted_ids.append(victim_id)
                    self._stats.evictions += 1
                self._store[session_id] = state

            self._stats.issued += 1
            return issue_ticket(
                self._key, session_id, protocol_version, cipher_suite,
                state.issued_at, state.expires_at,
            )

    def resume(self, ticket: str, protocol_version: str,
               cipher_suite: str) -> ResumeResult:
        """Attempt to resume a session, validating signature then bindings."""
        with self._lock:
            now = self._clock()

            # 1) Authenticate the ticket before touching the cache.
            try:
                payload = verify_ticket(self._key, ticket)
            except TicketError as exc:
                text = str(exc)
                if "signature" in text:
                    reason = RejectReason.FORGED
                else:
                    reason = RejectReason.MALFORMED
                return self._reject(reason, text)

            # 2) Ticket-level expiry (valid signature, past exp claim).
            if now >= payload["exp"]:
                self._store.pop(payload["sid"], None)
                return self._reject(
                    RejectReason.EXPIRED_TICKET,
                    "ticket expired at %.3f (now %.3f)"
                    % (payload["exp"], now),
                )

            # 3) Cache lookup (may have been LRU-evicted or lazily expired).
            state = self._store.get(payload["sid"])
            if state is None:
                if self._was_known(payload["sid"]):
                    reason = RejectReason.EVICTED
                    detail = "session state was evicted from the cache"
                else:
                    reason = RejectReason.UNKNOWN_SESSION
                    detail = "session id not found in cache"
                return self._reject(reason, detail)

            if state.is_expired(now):
                del self._store[state.session_id]
                self._stats.lazy_expiries += 1
                return self._reject(
                    RejectReason.EXPIRED_IN_CACHE,
                    "cached state expired at %.3f (now %.3f)"
                    % (state.expires_at, now),
                )

            # 4) Binding checks: protocol version then cipher suite.
            if protocol_version != payload["ver"] \
                    or protocol_version != state.protocol_version:
                return self._reject(
                    RejectReason.PROTOCOL_MISMATCH,
                    "offered %r but session bound to %r"
                    % (protocol_version, state.protocol_version),
                )
            if cipher_suite != payload["cs"] \
                    or cipher_suite != state.cipher_suite:
                return self._reject(
                    RejectReason.CIPHER_MISMATCH,
                    "offered %r but session bound to %r"
                    % (cipher_suite, state.cipher_suite),
                )

            # 5) Success -- refresh LRU recency.
            self._store.move_to_end(state.session_id)
            self._stats.reused += 1
            return ResumeResult(
                ResumeStatus.REUSED, None,
                "session %s resumed" % state.session_id, state,
            )

    # ----------------------------------------------------------------- utils

    def _reject(self, reason: RejectReason, detail: str) -> ResumeResult:
        self._stats.rejected += 1
        self._stats.by_reason[reason.value] = \
            self._stats.by_reason.get(reason.value, 0) + 1
        if reason in (RejectReason.FORGED, RejectReason.MALFORMED):
            self._stats.forged_or_malformed += 1
        return ResumeResult(ResumeStatus.REJECTED, reason, detail)

    def _was_known(self, session_id: str) -> bool:
        return session_id in self._evicted_ids
