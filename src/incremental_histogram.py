"""Incremental equi-depth histogram for selectivity estimation.

Standard library only.

Design
------
The value domain [low, high] is fixed at construction time. The histogram
is a list of buckets that always partition the whole domain. Bucket
boundaries are *not* recomputed from scratch on every update; they only
change through two local operations:

* split  -- when one bucket's count grows beyond ``split_factor`` times the
            average bucket count (and the bucket capacity ``max_buckets``
            has not been reached), the bucket is split at its midpoint and
            its count is divided proportionally to the two halves.
* merge  -- when deletes shrink the data, the adjacent bucket pair with the
            smallest combined count is merged if that combined count is
            below the average bucket count. Merging is lossless (counts add).

The gap between the split threshold (2x average) and the merge threshold
(1x average) provides hysteresis so buckets do not oscillate.

Estimation uses the *continuous uniform spread* assumption: inside a
bucket, values are assumed uniformly distributed, so a range query that
covers a fraction f of a bucket's width is estimated to contain
f * bucket.count rows. This assumption is the single source of estimation
error; it is exact for truly uniform intra-bucket data and degrades when
the intra-bucket distribution is skewed and the bucket has not (yet) been
split enough to isolate the hot region.

Maintenance contract: ``delete(v)`` must correspond to a previously
inserted value (as in a real DBMS, where deletes carry the actual row).
Because splits place counts under the uniform-spread assumption, a valid
delete can land on a bucket whose count drifted to zero; the delete is
then charged to the nearest non-empty bucket (drift repair). A delete is
rejected only when the whole histogram is empty.
"""

from bisect import bisect_right, insort

__all__ = ["Bucket", "IncrementalHistogram"]


class Bucket:
    """Half-open interval [low, high) with an exact row count."""

    __slots__ = ("low", "high", "count")

    def __init__(self, low, high, count=0):
        self.low = low
        self.high = high
        self.count = count

    @property
    def width(self):
        return self.high - self.low

    def __repr__(self):
        return "Bucket([%g, %g), count=%d)" % (self.low, self.high, self.count)


class IncrementalHistogram:
    def __init__(self, low, high, max_buckets=16, split_factor=2.0):
        if not low < high:
            raise ValueError("require low < high")
        if max_buckets < 1:
            raise ValueError("max_buckets must be >= 1")
        if split_factor <= 1.0:
            raise ValueError("split_factor must be > 1 (hysteresis)")
        self.low = low
        self.high = high
        self.max_buckets = max_buckets
        self.split_factor = split_factor
        self.buckets = [Bucket(low, high)]
        self._lows = [low]          # sorted bucket lower bounds, for bisect
        self.total = 0              # exact number of live rows
        self.num_splits = 0         # instrumentation
        self.num_merges = 0         # instrumentation

    # ------------------------------------------------------------- updates
    def insert(self, value):
        self._check_domain(value)
        idx = self._find_bucket(value)
        self.buckets[idx].count += 1
        self.total += 1
        self._maybe_split(idx)

    def delete(self, value):
        self._check_domain(value)
        idx = self._find_bucket(value)
        if self.buckets[idx].count <= 0:
            # Splitting divided counts under the uniform-spread assumption,
            # so the true row may sit in a half whose count went to a
            # sibling. Repair the drift: charge the delete to the nearest
            # non-empty bucket. Totals stay exact; only placement is
            # approximate, which the estimator already assumes.
            idx = self._nearest_non_empty(idx)
            if idx is None:
                raise ValueError("delete of value %r but histogram is empty" % (value,))
        self.buckets[idx].count -= 1
        self.total -= 1
        self._maybe_merge()

    def _nearest_non_empty(self, idx):
        for dist in range(1, len(self.buckets)):
            for cand in (idx - dist, idx + dist):
                if 0 <= cand < len(self.buckets) and self.buckets[cand].count > 0:
                    return cand
        return None

    def update(self, old_value, new_value):
        """Replace one row's value (delete + insert)."""
        self.delete(old_value)
        self.insert(new_value)

    # ----------------------------------------------------------- estimation
    def estimate_range(self, a, b):
        """Estimated number of rows with a <= v < b (clamped to domain)."""
        a = max(a, self.low)
        b = min(b, self.high)
        if a >= b:
            return 0.0
        est = 0.0
        for bucket in self.buckets:
            lo = a if a > bucket.low else bucket.low
            hi = b if b < bucket.high else bucket.high
            if hi > lo:
                est += bucket.count * (hi - lo) / bucket.width
        return est

    def estimate_point(self, v):
        """Estimated density at v (count per unit width of its bucket)."""
        self._check_domain(v)
        bucket = self.buckets[self._find_bucket(v)]
        return bucket.count / bucket.width

    def selectivity(self, a, b):
        """Estimated fraction of rows in [a, b)."""
        if self.total == 0:
            return 0.0
        return self.estimate_range(a, b) / self.total

    # ------------------------------------------------------------ internals
    def _check_domain(self, value):
        if not (self.low <= value <= self.high):
            raise ValueError("value %r outside domain [%g, %g]" % (value, self.low, self.high))

    def _find_bucket(self, value):
        idx = bisect_right(self._lows, value) - 1
        return max(0, min(idx, len(self.buckets) - 1))

    def _target(self):
        """Average bucket count if all capacity were in use."""
        return max(self.total, 1) / self.max_buckets

    def _maybe_split(self, idx):
        if len(self.buckets) >= self.max_buckets:
            return
        bucket = self.buckets[idx]
        if bucket.count <= self.split_factor * self._target():
            return
        mid = (bucket.low + bucket.high) / 2.0
        if mid == bucket.low or mid == bucket.high:
            return  # bucket too narrow to split further
        left_count = bucket.count // 2
        right_count = bucket.count - left_count  # total preserved exactly
        right = Bucket(mid, bucket.high, right_count)
        bucket.high = mid
        bucket.count = left_count
        self.buckets.insert(idx + 1, right)
        self._lows.insert(idx + 1, mid)
        self.num_splits += 1

    def _maybe_merge(self):
        while len(self.buckets) > 1:
            target = self._target()
            best_i, best_sum = -1, None
            for i in range(len(self.buckets) - 1):
                pair = self.buckets[i].count + self.buckets[i + 1].count
                if best_sum is None or pair < best_sum:
                    best_i, best_sum = i, pair
            if best_sum >= target:
                return
            left = self.buckets[best_i]
            right = self.buckets.pop(best_i + 1)
            self._lows.pop(best_i + 1)
            left.high = right.high
            left.count += right.count
            self.num_merges += 1

    # ---------------------------------------------------------- diagnostics
    def snapshot(self):
        """List of (low, high, count) tuples, useful for tests/logging."""
        return [(b.low, b.high, b.count) for b in self.buckets]

    def check_invariants(self):
        """Raise AssertionError if any structural invariant is violated."""
        assert 1 <= len(self.buckets) <= self.max_buckets, "capacity bound violated"
        assert self.buckets[0].low == self.low
        assert self.buckets[-1].high == self.high
        assert sum(b.count for b in self.buckets) == self.total, "count not conserved"
        for prev, nxt in zip(self.buckets, self.buckets[1:]):
            assert prev.high == nxt.low, "gap or overlap between buckets"
            assert prev.low < prev.high, "degenerate bucket"
        assert self._lows == sorted(self._lows)
        return True
