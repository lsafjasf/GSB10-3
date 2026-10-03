"""Flow record aggregation library (standard library only).

Merge rules
-----------
* Five-tuple key: (proto, src_ip, src_port, dst_ip, dst_port).
* Records of the same key, ordered by (start_ts, end_ts, arrival seq), belong
  to one session while the gap between the previous record's *end* and the
  next record's *start* satisfies::

        gap = next.start_ts - prev.end_ts
        gap <= idle_timeout   -> same session (merge)
        gap >  idle_timeout   -> new session (split)

  Boundary: ``gap == idle_timeout`` MERGES; only a strictly larger gap splits.
  ``gap <= 0`` (clocks overlap / reordered arrival) always merges.
* Session counters are sums of bytes/packets of merged records; start is the
  minimum start_ts and end is the maximum end_ts.
* Out-of-order arrival: per key we keep a chain of sessions sorted by start.
  A late record is inserted in start order and repeatedly merged with the
  predecessor / successor whenever the gap rule allows it. This yields the
  exact same sessions as processing all records in sorted order (verified by
  the differential test in differential_test.py).

Aging / memory bound
--------------------
* ``sweep(now)`` expires every key whose last seen record end satisfies
  ``last_seen < now - idle_timeout`` (strict; equality is still alive,
  matching the merge boundary) and returns the finalized sessions.
* ``max_active_sessions`` caps the number of *keys* kept in memory; overflow
  evicts the globally least-recently-touched key (LRU, O(log n) lazy heap).
  Therefore live memory is bounded regardless of export volume.
* Time is injectable: pass ``clock=`` to the constructor and/or ``now=`` to
  ``sweep``. With no injection the library uses ``time.monotonic``.
"""

from __future__ import annotations

import heapq
import time
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

FiveTuple = Tuple[str, str, int, str, int]


@dataclass(frozen=True)
class FlowRecord:
    """One raw flow record exported by a device."""

    proto: str
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    start_ts: float
    end_ts: float
    byte_count: int
    packet_count: int
    seq: int = 0  # arrival order, used only as a deterministic tie-break

    @property
    def key(self) -> FiveTuple:
        return (self.proto, self.src_ip, self.src_port,
                self.dst_ip, self.dst_port)

    def __post_init__(self) -> None:
        if self.end_ts < self.start_ts:
            raise ValueError("end_ts must be >= start_ts")
        if self.byte_count < 0 or self.packet_count < 0:
            raise ValueError("counters must be non-negative")


@dataclass
class FlowSession:
    """An aggregated (merged) session."""

    key: FiveTuple
    session_id: int
    start_ts: float
    end_ts: float
    byte_count: int = 0
    packet_count: int = 0
    record_count: int = 0
    # last activity time on the record clock (max end_ts of merged records)
    last_seen: float = 0.0

    @property
    def five_tuple(self) -> FiveTuple:
        return self.key

    def as_row(self) -> Tuple:
        return (*self.key, self.session_id, self.start_ts, self.end_ts,
                self.byte_count, self.packet_count, self.record_count)


@dataclass
class _KeyState:
    # sessions kept sorted by (start_ts, end_ts); invariant: adjacent sessions
    # are separated by a gap strictly greater than idle_timeout
    chain: List[FlowSession] = field(default_factory=list)
    last_seen: float = 0.0
    recency: int = 0  # global LRU touch counter


def _absorb(session: FlowSession, rec: FlowRecord) -> None:
    """Merge a record into an existing session."""
    if rec.start_ts < session.start_ts:
        session.start_ts = rec.start_ts
    if rec.end_ts > session.end_ts:
        session.end_ts = rec.end_ts
    if rec.end_ts > session.last_seen:
        session.last_seen = rec.end_ts
    session.byte_count += rec.byte_count
    session.packet_count += rec.packet_count
    session.record_count += 1


def _fuse(a: FlowSession, b: FlowSession) -> FlowSession:
    """Fuse two sessions known to belong together."""
    return FlowSession(
        key=a.key,
        session_id=a.session_id,
        start_ts=min(a.start_ts, b.start_ts),
        end_ts=max(a.end_ts, b.end_ts),
        byte_count=a.byte_count + b.byte_count,
        packet_count=a.packet_count + b.packet_count,
        record_count=a.record_count + b.record_count,
        last_seen=max(a.last_seen, b.last_seen),
    )


class FlowAggregator:
    """Incremental flow aggregator with idle splitting, aging and a hard
    memory bound.

    Parameters
    ----------
    idle_timeout:
        Maximum allowed idle gap (same units as record timestamps). Records
        with ``gap <= idle_timeout`` merge; a strictly larger gap starts a new
        session.
    max_active_sessions:
        Maximum number of live five-tuple keys. Overflow evicts the LRU key.
    clock:
        Injectable wall-clock callable used by ``sweep()`` without ``now``.
    """

    def __init__(
        self,
        idle_timeout: float,
        max_active_sessions: int = 1_000_000,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if idle_timeout < 0:
            raise ValueError("idle_timeout must be >= 0")
        if max_active_sessions < 1:
            raise ValueError("max_active_sessions must be >= 1")
        self.idle_timeout = float(idle_timeout)
        self.max_active_sessions = max_active_sessions
        self._clock = clock or time.monotonic

        self._states: Dict[FiveTuple, _KeyState] = {}
        self._next_session_id = 1
        self._touch_counter = 0
        # lazy LRU heap: entries are (recency, key); an entry is valid only
        # while it matches the key's current recency
        self._heap: List[Tuple[int, FiveTuple]] = []

        # stats
        self.expired_keys = 0
        self.evicted_keys = 0
        self.finalized_sessions = 0

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def add(self, rec: FlowRecord) -> List[FlowSession]:
        """Add one record. Returns sessions finalized by LRU eviction
        (empty unless the capacity bound is hit)."""
        key = rec.key
        st = self._states.get(key)
        created_key = st is None
        if created_key:
            st = _KeyState()
            self._states[key] = st

        self._insert_record(st, rec)
        st.last_seen = max(st.last_seen, rec.end_ts)
        self._touch(key, st)

        evicted: List[FlowSession] = []
        if created_key and len(self._states) > self.max_active_sessions:
            evicted = self._evict_lru()
        return evicted

    def sweep(self, now: Optional[float] = None) -> List[FlowSession]:
        """Expire keys idle for strictly more than ``idle_timeout`` at ``now``
        and return their finalized sessions.

        Cutoff criterion: ``last_seen < now - idle_timeout`` expires;
        ``last_seen == now - idle_timeout`` is retained (boundary aligns with
        the merge rule ``gap <= idle_timeout``).
        """
        if now is None:
            now = self._clock()
        cutoff = now - self.idle_timeout
        dead = [k for k, st in self._states.items() if st.last_seen < cutoff]
        out: List[FlowSession] = []
        for key in dead:
            out.extend(self._finalize_key(key))
            self.expired_keys += 1
        return out

    def flush(self) -> List[FlowSession]:
        """Finalize and remove every live session (order: key, start time)."""
        out: List[FlowSession] = []
        for key in sorted(self._states):
            out.extend(self._states[key].chain)
        self._states.clear()
        self._heap.clear()
        self.finalized_sessions += len(out)
        return out

    def active_keys(self) -> int:
        return len(self._states)

    def active_sessions(self) -> int:
        return sum(len(st.chain) for st in self._states.values())

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _new_session(self, key: FiveTuple, rec: FlowRecord) -> FlowSession:
        s = FlowSession(
            key=key,
            session_id=self._next_session_id,
            start_ts=rec.start_ts,
            end_ts=rec.end_ts,
            byte_count=rec.byte_count,
            packet_count=rec.packet_count,
            record_count=1,
            last_seen=rec.end_ts,
        )
        self._next_session_id += 1
        return s

    def _insert_record(self, st: _KeyState, rec: FlowRecord) -> None:
        chain = st.chain
        if not chain:
            chain.append(self._new_session(rec.key, rec))
            return

        # insertion position by (start_ts, end_ts)
        i = bisect_left(chain, (rec.start_ts, rec.end_ts),
                        key=lambda s: (s.start_ts, s.end_ts))

        merged: Optional[FlowSession] = None
        pos = i
        # merge with predecessor if the gap rule allows it
        if i > 0 and rec.start_ts - chain[i - 1].end_ts <= self.idle_timeout:
            merged = chain[i - 1]
            pos = i - 1
        # merge with successor if the gap rule allows it (a long/overlapping
        # record may bridge both neighbours, in which case everything fuses)
        if i < len(chain) and chain[i].start_ts - rec.end_ts <= self.idle_timeout:
            if merged is None:
                merged = chain[i]
                pos = i
            else:
                merged = _fuse(merged, chain[i])
                del chain[i]  # successor shifts to position i
        if merged is not None:
            _absorb(merged, rec)
            chain[pos] = merged
            # cascading merges: the grown session may now reach further ones
            self._cascade(chain, pos)
        else:
            chain.insert(i, self._new_session(rec.key, rec))

    def _cascade(self, chain: List[FlowSession], i: int) -> None:
        # grow rightward while the gap rule to the next session holds
        while i + 1 < len(chain):
            if chain[i + 1].start_ts - chain[i].end_ts <= self.idle_timeout:
                chain[i] = _fuse(chain[i], chain[i + 1])
                del chain[i + 1]
            else:
                break
        # grow leftward while the gap rule to the previous session holds
        while i > 0:
            if chain[i].start_ts - chain[i - 1].end_ts <= self.idle_timeout:
                chain[i - 1] = _fuse(chain[i - 1], chain[i])
                del chain[i]
                i -= 1
            else:
                break

    def _touch(self, key: FiveTuple, st: _KeyState) -> None:
        self._touch_counter += 1
        st.recency = self._touch_counter
        heapq.heappush(self._heap, (st.recency, key))
        # bound heap bookkeeping memory: rebuild when stale entries dominate
        if len(self._heap) > 4 * self.max_active_sessions:
            self._rebuild_heap()

    def _rebuild_heap(self) -> None:
        self._heap = [(st.recency, key) for key, st in self._states.items()]
        heapq.heapify(self._heap)

    def _evict_lru(self) -> List[FlowSession]:
        out: List[FlowSession] = []
        while len(self._states) > self.max_active_sessions:
            while self._heap:
                recency, key = heapq.heappop(self._heap)
                st = self._states.get(key)
                if st is not None and st.recency == recency:
                    break
            else:
                # defensive: heap exhausted, rebuild and retry once
                self._rebuild_heap()
                recency, key = heapq.heappop(self._heap)
            out.extend(self._finalize_key(key))
            self.evicted_keys += 1
        return out

    def _finalize_key(self, key: FiveTuple) -> List[FlowSession]:
        st = self._states.pop(key)
        self.finalized_sessions += len(st.chain)
        return st.chain
