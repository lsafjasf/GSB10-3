"""Incremental equi-depth histogram with fixed bucket boundaries.

Only the Python standard library is used.

Design
------
The value domain ``[low, high]`` is partitioned into disjoint, ordered
buckets with *fixed* boundaries.  Every bucket stores:

* its boundary ``[lo, hi)`` (the last bucket includes ``high``),
* the number of tuples currently in it,
* the per-value counts inside it, so a split can be computed from data that
  actually live in the bucket instead of assuming uniformity.

Inserts/deletes touch only the bucket containing the value (O(log B) to
locate it).  A maintenance pass then performs *local* split/merge work, so
the histogram is never rebuilt from scratch:

* ``insert`` -> a bucket that grows above ``split_factor * N/M`` is split in
  two (equi-depth style, at the median split point); if all ``M`` bucket
  slots are taken, the lightest adjacent pair is merged first (this is the
  classic split/merge equi-depth maintenance rule).
* ``delete`` -> a bucket that shrinks below ``N/(M*merge_factor)`` is merged
  with its lighter neighbour.

Range selectivity is estimated by summing fully covered buckets and
interpolating partially covered ones under a uniform-spread assumption.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class Bucket:
    """A fixed-boundary bucket covering ``[lo, hi)`` (last one inclusive)."""

    lo: float
    hi: float
    count: int = 0
    freq: Dict[float, int] = field(default_factory=dict)

    def width(self) -> float:
        return self.hi - self.lo

    def can_split(self) -> bool:
        # There must be room for a distinct cut point strictly inside.
        if self.hi <= self.lo:
            return False
        mid = (self.lo + self.hi) / 2.0
        return self.lo < mid < self.hi


class IncrementalHistogram:
    """Equi-depth style histogram maintained incrementally.

    Parameters
    ----------
    low, high:
        Inclusive endpoints of the value domain.
    max_buckets:
        Hard capacity upper bound on the number of buckets (M >= 2).
    split_factor:
        A bucket is "heavy" once its count exceeds
        ``split_factor * total / max_buckets``.
    merge_factor:
        A bucket is "light" once its count is below
        ``total / (max_buckets * merge_factor)``.
    """

    def __init__(
        self,
        low: float,
        high: float,
        max_buckets: int = 16,
        split_factor: float = 2.0,
        merge_factor: float = 2.0,
    ) -> None:
        if high <= low:
            raise ValueError("high must be strictly greater than low")
        if max_buckets < 2:
            raise ValueError("max_buckets must be >= 2")
        if split_factor <= 1.0:
            raise ValueError("split_factor must be > 1")
        if merge_factor <= 1.0:
            raise ValueError("merge_factor must be > 1")

        self.low = float(low)
        self.high = float(high)
        self.max_buckets = max_buckets
        self.split_factor = split_factor
        self.merge_factor = merge_factor

        first = Bucket(self.low, self.high, 0, {})
        self._buckets: List[Bucket] = [first]
        self._edges: List[float] = [self.low, self.high]
        self._total: int = 0
        # Append-only structural trace, handy for demos and debugging.
        self.events: List[str] = []

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def total(self) -> int:
        """Number of values currently represented."""
        return self._total

    def __len__(self) -> int:
        return len(self._buckets)

    def buckets(self) -> Sequence[Bucket]:
        """Return the current ordered buckets (boundaries are fixed/immutable)."""
        return tuple(self._buckets)

    def edges(self) -> Tuple[float, ...]:
        return tuple(self._edges)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def insert(self, value: float, multiplicity: int = 1) -> None:
        """Insert ``multiplicity`` copies of ``value`` (default one)."""
        if multiplicity <= 0:
            raise ValueError("multiplicity must be positive")
        v = self._check_domain(value)
        idx = self._bucket_index(v)
        bucket = self._buckets[idx]
        bucket.count += multiplicity
        bucket.freq[v] = bucket.freq.get(v, 0) + multiplicity
        self._total += multiplicity
        self._rebalance(idx, after_insert=True)

    def delete(self, value: float, multiplicity: int = 1) -> None:
        """Delete ``multiplicity`` copies of ``value``.

        Raises ``KeyError`` if fewer than that are currently present.
        """
        if multiplicity <= 0:
            raise ValueError("multiplicity must be positive")
        v = self._check_domain(value)
        idx = self._bucket_index(v)
        bucket = self._buckets[idx]
        have = bucket.freq.get(v, 0)
        if have < multiplicity:
            raise KeyError(
                f"cannot delete {multiplicity} copies of {value!r}; only {have} present"
            )
        bucket.count -= multiplicity
        self._total -= multiplicity
        if have == multiplicity:
            del bucket.freq[v]
        else:
            bucket.freq[v] = have - multiplicity
        self._rebalance(idx, after_insert=False)

    def _check_domain(self, value: float) -> float:
        v = float(value)
        if v < self.low or v > self.high:
            raise ValueError(f"value {value!r} outside domain [{self.low}, {self.high}]")
        return v

    def _bucket_index(self, v: float) -> int:
        # Edges [e0, e1, ...]; bucket i covers [e_i, e_{i+1}), last one closed.
        idx = bisect.bisect_right(self._edges, v) - 1
        if idx >= len(self._buckets):
            idx = len(self._buckets) - 1
        return idx

    # ------------------------------------------------------------------
    # Local split / merge maintenance (no full rebuild)
    # ------------------------------------------------------------------

    def _ideal_bucket_count(self) -> float:
        return self._total / self.max_buckets

    def _heavy_limit(self) -> float:
        return self.split_factor * self._ideal_bucket_count()

    def _light_limit(self) -> float:
        ideal = self._ideal_bucket_count()
        return ideal / self.merge_factor if self._total > 0 else 0.0

    def _heaviest_index(self) -> int:
        return max(range(len(self._buckets)), key=lambda i: self._buckets[i].count)

    def _merge_at(self, i: int) -> None:
        """Merge buckets i and i+1 into a single fixed-boundary bucket."""
        left, right = self._buckets[i], self._buckets[i + 1]
        merged_freq = dict(left.freq)
        for v, c in right.freq.items():
            merged_freq[v] = merged_freq.get(v, 0) + c
        merged = Bucket(left.lo, right.hi, left.count + right.count, merged_freq)
        self._buckets[i : i + 2] = [merged]
        del self._edges[i + 1]
        self.events.append(
            f"merge  [{left.lo:g}, {left.hi:g}) ({left.count}) + "
            f"[{right.lo:g}, {right.hi:g}) ({right.count}) "
            f"-> [{merged.lo:g}, {merged.hi:g}) ({merged.count})"
        )

    def _split_at(self, i: int) -> Optional[int]:
        """Split bucket i at an equi-depth cut; return index of the left half."""
        bucket = self._buckets[i]
        if not bucket.can_split() or bucket.count == 0:
            return None

        lo, hi = bucket.lo, bucket.hi
        target = bucket.count / 2.0

        # Candidate cuts: midpoint of the bucket and every gap between
        # adjacent distinct values, so the median boundary is reachable.
        values = sorted(bucket.freq)
        cuts = {(lo + hi) / 2.0}
        for a, b in zip(values, values[1:]):
            if a < b:
                cuts.add((float(a) + float(b)) / 2.0)

        best_cut, best_diff = None, None
        cumulative = 0
        ordered_cuts = sorted(cuts)
        cut_idx = 0
        for v in values:
            while cut_idx < len(ordered_cuts) and ordered_cuts[cut_idx] <= v:
                c = ordered_cuts[cut_idx]
                if lo < c < hi:
                    diff = abs(cumulative - target)
                    if best_diff is None or diff < best_diff:
                        best_diff, best_cut = diff, c
                cut_idx += 1
            cumulative += bucket.freq[v]
        while cut_idx < len(ordered_cuts):
            c = ordered_cuts[cut_idx]
            if lo < c < hi:
                diff = abs(cumulative - target)
                if best_diff is None or diff < best_diff:
                    best_diff, best_cut = diff, c
            cut_idx += 1

        if best_cut is None:
            return None

        cut = best_cut
        left_freq, right_freq = {}, {}
        left_count = right_count = 0
        for v, c in bucket.freq.items():
            if v < cut:
                left_freq[v] = c
                left_count += c
            else:
                right_freq[v] = c
                right_count += c
        if not left_freq or not right_freq:
            return None

        left = Bucket(lo, cut, left_count, left_freq)
        right = Bucket(cut, hi, right_count, right_freq)
        self._buckets[i : i + 1] = [left, right]
        self._edges.insert(i + 1, cut)
        self.events.append(
            f"split  [{lo:g}, {hi:g}) ({bucket.count}) "
            f"-> [{lo:g}, {cut:g}) ({left_count}) + [{cut:g}, {hi:g}) ({right_count})"
        )
        return i

    def _rebalance(self, changed_idx: int, after_insert: bool) -> None:
        # Bounded work per update; the constants below make the pass finite.
        max_steps = 2 * self.max_buckets + 4
        for _ in range(max_steps):
            if after_insert:
                heavy = self._heaviest_index()
                if self._buckets[heavy].count <= self._heavy_limit():
                    return
                if len(self._buckets) >= self.max_buckets:
                    # Capacity is full: make room by merging the lightest pair,
                    # unless that pair is the heavy bucket itself (no benefit).
                    if len(self._buckets) < 2:
                        return
                    best_i, best_count = None, None
                    for i in range(len(self._buckets) - 1):
                        combined = self._buckets[i].count + self._buckets[i + 1].count
                        if best_count is None or combined < best_count:
                            best_count, best_i = combined, i
                    heavy_pair = (
                        best_i == heavy or best_i + 1 == heavy
                    )
                    if heavy_pair:
                        return
                    self._merge_at(best_i)
                    continue
                result = self._split_at(heavy)
                if result is None:
                    return
            else:
                if self._total == 0:
                    while len(self._buckets) > 1:
                        self._merge_at(0)
                    return
                light = None
                limit = self._light_limit()
                for i, b in enumerate(self._buckets):
                    if b.count < limit:
                        light = i
                        break
                if light is None or len(self._buckets) < 2:
                    return
                left_count = (
                    self._buckets[light - 1].count if light > 0 else None
                )
                right_count = (
                    self._buckets[light + 1].count
                    if light + 1 < len(self._buckets)
                    else None
                )
                if left_count is None:
                    merge_i = light
                elif right_count is None or left_count <= right_count:
                    merge_i = light - 1
                else:
                    merge_i = light
                self._merge_at(merge_i)

    # ------------------------------------------------------------------
    # Selectivity estimation
    # ------------------------------------------------------------------

    def estimate(self, qlow: float, qhigh: float) -> float:
        """Estimated number of values in range ``[qlow, qhigh)``.

        Fully covered buckets contribute their exact maintained count;
        partially covered buckets are interpolated by the fraction of the
        bucket width overlapped (uniform-spread assumption).
        """
        if qhigh <= qlow or self._total == 0:
            return 0.0
        qlow = max(float(qlow), self.low)
        qhigh = min(float(qhigh), self.high)
        if qhigh <= qlow:
            return 0.0

        estimate_sum = 0.0
        start = bisect.bisect_right(self._edges, qlow) - 1
        start = max(start, 0)
        end = bisect.bisect_right(self._edges, qhigh) - 1
        end = min(end, len(self._buckets) - 1)

        for i in range(start, end + 1):
            b = self._buckets[i]
            overlap_lo = max(qlow, b.lo)
            overlap_hi = min(qhigh, b.hi)
            if overlap_lo <= b.lo and overlap_hi >= b.hi:
                estimate_sum += b.count
            else:
                width = b.width()
                frac = (overlap_hi - overlap_lo) / width if width > 0 else 0.0
                estimate_sum += frac * b.count
        return estimate_sum

    def estimate_point(self, value: float) -> float:
        """Estimated frequency of a single value.

        A bucket's count is spread uniformly over its width.  For discrete
        domains divide additionally by the number of unit positions.
        """
        v = float(value)
        if v < self.low or v > self.high or self._total == 0:
            return 0.0
        idx = self._bucket_index(v)
        b = self._buckets[idx]
        width = b.width()
        if width <= 0:
            return float(b.count)
        return b.count / width
