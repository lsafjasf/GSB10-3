"""Corpus maintenance for a feedback-driven fuzzer (stdlib only).

Three maintenance primitives are provided:

1. Coverage-gain gating
   A candidate is stored only when its edge coverage adds at least one edge
   the corpus does not already cover. Exact byte duplicates and inputs whose
   coverage is already fully covered are rejected.

2. Capacity bound + redundant-input eviction
   The corpus keeps at most ``max_size`` entries. When it is full, an entry is
   evicted only if it is *fully redundant*: every edge it covers is also
   covered by the remaining entries together with the new candidate. Such an
   eviction never loses coverage. Among fully redundant entries the largest
   one is evicted first (smallest inputs are cheaper to store/replay). If no
   fully redundant entry exists, the new candidate is rejected -- coverage is
   never traded away.

3. Coverage-preserving minimization
   Before storage every accepted input is shrunk with a delta-debugging style
   loop (classic ddmin: chunk removal at varying granularity). Shrinking keeps
   only candidates whose coverage is a superset of the original coverage.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections import Counter
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

Coverage = FrozenSet[int]
CoverageFn = Callable[[bytes], Optional[Coverage]]


@dataclasses.dataclass
class Entry:
    """One stored corpus input."""

    data: bytes
    edges: Coverage
    sha256: str
    stored_size: int          # bytes actually stored (after minimization)
    original_size: int        # bytes offered by the fuzzer (before minimization)
    seq: int                  # insertion order

    def unique_edges(self, covered_by_others: Coverage) -> Coverage:
        """Edges this entry contributes beyond everything else covers."""
        return self.edges - covered_by_others


@dataclasses.dataclass
class EvictionReport:
    """Coverage comparison around one eviction event."""

    evicted_seq: int
    evicted_stored_size: int
    evicted_edges: int
    coverage_before: int
    coverage_after: int
    corpus_size_before: int
    corpus_size_after: int
    bytes_before: int
    bytes_after: int
    coverage_lost: int = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.coverage_lost = max(0, self.coverage_before - self.coverage_after)

    def describe(self) -> str:
        return (
            f"evict entry#{self.evicted_seq} ({self.evicted_stored_size} B, "
            f"{self.evicted_edges} edges): coverage {self.coverage_before} -> "
            f"{self.coverage_after} edges (lost {self.coverage_lost}), "
            f"stored bytes {self.bytes_before} -> {self.bytes_after}"
        )


@dataclasses.dataclass
class Stats:
    """Admission counters; ``admission_rate`` is accepted / offered."""

    offered: int = 0
    accepted: int = 0
    rejected_invalid: int = 0
    rejected_duplicate: int = 0
    rejected_no_gain: int = 0
    rejected_full: int = 0
    evictions: int = 0
    bytes_offered: int = 0
    bytes_stored: int = 0
    bytes_accepted_original: int = 0  # pre-minimization size of accepted inputs

    @property
    def rejected(self) -> int:
        return (
            self.rejected_invalid
            + self.rejected_duplicate
            + self.rejected_no_gain
            + self.rejected_full
        )

    @property
    def admission_rate(self) -> float:
        return self.accepted / self.offered if self.offered else 0.0

    def describe(self) -> str:
        kept_pct = 100.0 * self.admission_rate
        shrink = (
            100.0 * (1 - self.bytes_stored / self.bytes_accepted_original)
            if self.bytes_accepted_original
            else 0.0
        )
        return (
            f"offered={self.offered} accepted={self.accepted} "
            f"rejected={self.rejected} (invalid={self.rejected_invalid}, "
            f"duplicate={self.rejected_duplicate}, no_gain={self.rejected_no_gain}, "
            f"full={self.rejected_full}) evictions={self.evictions} | "
            f"admission_rate={kept_pct:.2f}% | "
            f"minimization: accepted inputs {self.bytes_accepted_original}B -> "
            f"{self.bytes_stored}B stored (shrunk {shrink:.2f}%)"
        )


def minimize(data: bytes, coverage_fn: CoverageFn) -> Tuple[bytes, Coverage]:
    """Coverage-preserving delta debugging (ddmin).

    Returns the smallest input found (with its measured coverage) whose
    coverage is a superset of the original input's coverage. Empty input is
    returned if the original coverage was empty.
    """
    original = coverage_fn(data)
    if original is None:
        raise ValueError("cannot minimize an invalid input")
    if not original:
        return b"", frozenset()

    current = bytes(data)
    n = 2
    while len(current) >= 2:
        chunk_len = max(1, len(current) // n)
        reduced = False
        start = 0
        while start < len(current):
            end = min(len(current), start + chunk_len)
            candidate = current[:start] + current[end:]
            cov = coverage_fn(candidate)
            if cov is not None and original <= cov:
                current = candidate
                n = max(n - 1, 2)
                reduced = True
                break
            start = end
        if reduced:
            continue
        if n < len(current):
            n = min(n * 2, len(current))
        else:
            break
    return current, coverage_fn(current)


class Corpus:
    """Bounded, coverage-gated, self-minimizing fuzzing corpus."""

    def __init__(self, max_size: int, coverage_fn: CoverageFn) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.max_size = max_size
        self.coverage_fn = coverage_fn
        self._entries: Dict[str, Entry] = {}
        self._seen = set()  # sha256 of every valid raw input ever offered
        self._union: Counter = Counter()  # edge -> number of entries covering it
        self._seq = 0
        self.stats = Stats()
        self.eviction_reports: List[EvictionReport] = []

    # ---- introspection ----------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def coverage(self) -> Coverage:
        return frozenset(self._union)

    @property
    def stored_bytes(self) -> int:
        return sum(e.stored_size for e in self._entries.values())

    def inputs(self) -> List[bytes]:
        return [e.data for e in self._entries.values()]

    # ---- internals --------------------------------------------------------

    def _insert(self, data: bytes, edges: Coverage, original_size: int) -> None:
        digest = hashlib.sha256(data).hexdigest()
        self._seq += 1
        self._entries[digest] = Entry(
            data=data,
            edges=edges,
            sha256=digest,
            stored_size=len(data),
            original_size=original_size,
            seq=self._seq,
        )
        self._union.update(edges)
        self.stats.accepted += 1
        self.stats.bytes_stored += len(data)
        self.stats.bytes_accepted_original += original_size

    def _remove(self, entry: Entry) -> None:
        del self._entries[entry.sha256]
        self._union.subtract(entry.edges)
        for edge in list(self._union):
            if self._union[edge] <= 0:
                del self._union[edge]
        self.stats.bytes_stored -= entry.stored_size

    def _find_redundant(self, incoming_edges: Coverage) -> Optional[Entry]:
        """Return the largest entry that is fully redundant once the
        incoming candidate is in the corpus, or None.

        An entry is fully redundant if every edge it covers is also covered
        by (the other entries) union (the incoming candidate). Evicting such
        an entry loses no coverage once the candidate is stored.
        """
        incoming = set(incoming_edges)
        victim: Optional[Entry] = None
        for entry in self._entries.values():
            remaining = Counter(self._union)
            remaining.subtract(entry.edges)
            covered_by_others = {edge for edge, count in remaining.items() if count > 0}
            if entry.edges <= (covered_by_others | incoming):
                if victim is None or entry.stored_size > victim.stored_size:
                    victim = entry
        return victim

    # ---- public API -------------------------------------------------------

    def add(self, data: bytes) -> str:
        """Offer one fuzzing input.

        Returns one of:
        ``invalid``, ``duplicate``, ``no_gain``, ``evicted_and_added``,
        ``added``, ``full`` (capacity full with nothing redundant to evict).
        """
        self.stats.offered += 1
        self.stats.bytes_offered += len(data)

        edges = self.coverage_fn(data)
        if edges is None:
            self.stats.rejected_invalid += 1
            return "invalid"

        raw_digest = hashlib.sha256(data).hexdigest()
        if raw_digest in self._seen or raw_digest in self._entries:
            self.stats.rejected_duplicate += 1
            return "duplicate"
        self._seen.add(raw_digest)

        if edges <= self.coverage:
            self.stats.rejected_no_gain += 1
            return "no_gain"

        shrunk, shrunk_edges = minimize(data, self.coverage_fn)

        digest = hashlib.sha256(shrunk).hexdigest()
        if digest in self._entries:
            # Minimization collapsed the candidate onto an existing input.
            self.stats.rejected_duplicate += 1
            return "duplicate"
        if shrunk_edges <= self.coverage:
            self.stats.rejected_no_gain += 1
            return "no_gain"

        outcome = "added"
        victim = None
        if len(self._entries) >= self.max_size:
            victim = self._find_redundant(shrunk_edges)
            if victim is None:
                self.stats.rejected_full += 1
                return "full"
            outcome = "evicted_and_added"
            coverage_before = len(self._union)
            size_before = len(self._entries)
            bytes_before = self.stored_bytes
            victim_size = victim.stored_size
            victim_edges = len(victim.edges)
            victim_seq = victim.seq
            self._remove(victim)

        self._insert(shrunk, shrunk_edges, original_size=len(data))

        if victim is not None:
            report = EvictionReport(
                evicted_seq=victim_seq,
                evicted_stored_size=victim_size,
                evicted_edges=victim_edges,
                coverage_before=coverage_before,
                coverage_after=len(self._union),
                corpus_size_before=size_before,
                corpus_size_after=len(self._entries),
                bytes_before=bytes_before,
                bytes_after=self.stored_bytes,
            )
            self.eviction_reports.append(report)
            self.stats.evictions += 1
        return outcome

    def report(self) -> str:
        lines = [
            f"corpus: {len(self._entries)}/{self.max_size} entries, "
            f"{len(self._union)} edges covered, {self.stored_bytes} bytes stored",
            "stats:  " + self.stats.describe(),
        ]
        if self.eviction_reports:
            lines.append("evictions:")
            for rep in self.eviction_reports:
                lines.append("  - " + rep.describe())
        return "\n".join(lines)
