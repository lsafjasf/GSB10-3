"""Segmented LRU cache.

- Each segment owns an OrderedDict (per-segment LRU) and its own lock, so
  contending threads hash to different locks in expectation.
- Capacity is a single GLOBAL invariant (sum of all segment sizes), not a
  per-segment quota. On overflow the victim is evicted from the current
  segment (segment-local LRU); if that segment is empty, any non-empty
  segment supplies the victim, which guarantees the global bound.
- Key -> segment mapping is derived from a stable cryptographic digest, so
  it does not depend on Python's salted built-in hash and is identical
  across processes/interpreter restarts for the same key/segment count.
"""

import hashlib
import threading
from collections import OrderedDict

_MISS = object()


def stable_segment_index(key, segment_count):
    """Stable key -> [0, segment_count) mapping.

    Uses blake2b over a canonical key encoding. The same key always maps to
    the same index for a given segment count, independent of process,
    thread, insertion order, or PYTHONHASHSEED.
    """
    digest = hashlib.blake2b(_encode_key(key), digest_size=8).digest()
    return int.from_bytes(digest, "big") % segment_count


def _encode_key(key):
    if isinstance(key, bytes):
        return b"b:" + key
    if isinstance(key, str):
        return "s:".encode() + key.encode("utf-8")
    if isinstance(key, int):
        return ("i:%d" % key).encode("ascii")
    if isinstance(key, float):
        return ("f:%.17g" % key).encode("ascii")
    if isinstance(key, tuple):
        return b"t:" + b"(" + b",".join(_encode_key(k) for k in key) + b")"
    raise TypeError("unsupported key type: %r" % type(key).__name__)


class _Segment:
    __slots__ = ("lock", "store", "hits", "misses", "evictions")

    def __init__(self):
        self.lock = threading.Lock()
        self.store = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @property
    def size(self):
        return len(self.store)


class SegmentedCache:
    """Thread-safe segmented LRU cache with a global capacity bound."""

    def __init__(self, capacity, segment_count=16):
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive int, got %r" % (capacity,))
        if not isinstance(segment_count, int) or segment_count < 1:
            raise ValueError("segment_count must be a positive int, got %r"
                             % (segment_count,))
        self.capacity = capacity
        self.segment_count = segment_count
        self._segments = [_Segment() for _ in range(segment_count)]
        self._size = 0
        self._size_lock = threading.Lock()

    def segment_index(self, key):
        return stable_segment_index(key, self.segment_count)

    def segment_for(self, key):
        """Exposed so callers/tests can assert mapping stability."""
        return self._segments[self.segment_index(key)]

    def get(self, key, default=None):
        seg = self._segments[self.segment_index(key)]
        with seg.lock:
            value = seg.store.get(key, _MISS)
            if value is _MISS:
                seg.misses += 1
                return default
            seg.store.move_to_end(key)  # LRU touch
            seg.hits += 1
            return value

    def put(self, key, value):
        idx = self.segment_index(key)
        seg = self._segments[idx]

        with seg.lock:
            if key in seg.store:
                seg.store[key] = value
                seg.store.move_to_end(key)
            else:
                seg.store[key] = value
                with self._size_lock:
                    self._size += 1

        # Global bound, local-first eviction:
        # victim comes from the segment that just received the insert;
        # fall back to any non-empty segment if it cannot supply one.
        while self.size > self.capacity:
            victim = self._pick_victim(seg)
            with victim.lock:
                if victim.store:
                    victim.store.popitem(last=False)  # LRU oldest
                    victim.evictions += 1
                    with self._size_lock:
                        self._size -= 1

    def _pick_victim(self, preferred):
        if preferred.size > 0:
            return preferred
        for candidate in self._segments:
            if candidate.size > 0:
                return candidate
        return preferred  # store is empty; loop exits on next size check

    def __contains__(self, key):
        seg = self._segments[self.segment_index(key)]
        with seg.lock:
            return key in seg.store

    def __len__(self):
        return self.size

    @property
    def size(self):
        with self._size_lock:
            return self._size

    def clear(self):
        for seg in self._segments:
            with seg.lock:
                seg.store.clear()
                seg.hits = seg.misses = seg.evictions = 0
        with self._size_lock:
            self._size = 0

    def stats(self):
        """Global-perspective stats plus per-segment breakdown."""
        per_segment = []
        total = hits = misses = evictions = 0
        for i, seg in enumerate(self._segments):
            with seg.lock:
                size = seg.size
                per_segment.append({
                    "segment": i,
                    "size": size,
                    "hits": seg.hits,
                    "misses": seg.misses,
                    "evictions": seg.evictions,
                })
                total += size
                hits += seg.hits
                misses += seg.misses
                evictions += seg.evictions
        lookups = hits + misses
        return {
            "capacity": self.capacity,
            "segment_count": self.segment_count,
            "size": total,
            "hits": hits,
            "misses": misses,
            "evictions": evictions,
            "hit_rate": (hits / lookups) if lookups else 0.0,
            "per_segment": per_segment,
        }
