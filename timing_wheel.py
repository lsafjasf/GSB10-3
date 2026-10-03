"""Hierarchical timing wheel (stdlib only, injectable clock).

Design notes
------------
- Each level has ``1 << bits`` slots (default 256). Level ``i`` covers
  ``(1 << bits) ** i`` ticks per slot. Levels are appended on demand, so
  arbitrarily large delays are supported.
- A timer is inserted into the highest level whose slot range still covers
  its expiry. When the wheel's tick aligns with a level boundary, that
  level's current slot is *cascaded*: every live timer in it is re-inserted
  into a lower (more precise) level. Cascading runs from the highest level
  down so re-inserted timers can fall through several levels in one tick.
- Cancellation is lazy and O(1): the node is only flagged. Cancelled nodes
  are physically dropped the next time their slot is cascaded or fired, and
  they are never invoked.
- Timers expiring at the same tick fire in insertion order. Each node gets
  a monotonically increasing sequence number; the set of nodes that become
  due at one tick (immediate list + level-0 slot) is sorted by it.
- ``advance`` skips over empty ticks in O(events + boundaries) instead of
  stepping tick by tick, so huge delays and sparse loads stay fast.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol


class Clock(Protocol):
    """Injectable time source. Returns integer ticks (e.g. milliseconds)."""

    def time(self) -> int: ...


class VirtualClock:
    """Manually advanced clock for deterministic tests."""

    def __init__(self, start: int = 0) -> None:
        self._t = int(start)

    def time(self) -> int:
        return self._t

    def advance(self, delta: int) -> int:
        if delta < 0:
            raise ValueError("clock cannot move backwards")
        self._t += int(delta)
        return self._t


class MonotonicClock:
    """Real clock: integer milliseconds from time.monotonic_ns()."""

    def time(self) -> int:
        return time.monotonic_ns() // 1_000_000


@dataclass(eq=False)
class TimerNode:
    """Handle returned by TimingWheel.schedule*. O(1) lazy cancellation."""

    expiry: int
    seq: int
    callback: Callable[["TimerNode"], None]
    cancelled: bool = False
    fired: int = 0
    placements: List[int] = field(default_factory=list)

    def cancel(self) -> bool:
        """Lazy cancel: flag only. Returns False if already cancelled/fired."""
        if self.cancelled or self.fired:
            return False
        self.cancelled = True
        return True


class TimingWheel:
    def __init__(self, clock: Clock, bits: int = 8) -> None:
        if bits < 1:
            raise ValueError("bits must be >= 1")
        self._clock = clock
        self._bits = bits
        self._size = 1 << bits
        self._mask = self._size - 1
        self._levels: List[List[List[TimerNode]]] = [
            [[] for _ in range(self._size)]
        ]
        self._non_empty: List[set] = [set()]
        self._due: List[TimerNode] = []  # delay <= 0, fired in insertion order
        self._now = clock.time()
        self._seq = 0

    # ------------------------------------------------------------------ API

    @property
    def now(self) -> int:
        return self._now

    @property
    def levels(self) -> int:
        return len(self._levels)

    def schedule_after(self, delay: int, callback: Callable[[TimerNode], None]) -> TimerNode:
        """Schedule ``callback`` to fire at tick ``now + delay`` (delay >= 0)."""
        if delay < 0:
            raise ValueError("delay must be >= 0")
        return self.schedule_at(self._now + int(delay), callback)

    def schedule_at(self, expiry: int, callback: Callable[[TimerNode], None]) -> TimerNode:
        """Schedule ``callback`` to fire at absolute tick ``expiry``."""
        node = TimerNode(expiry=int(expiry), seq=self._seq, callback=callback)
        self._seq += 1
        self._insert(node)
        return node

    def advance(self, delta: int) -> int:
        """Advance the wheel by ``delta`` ticks, firing everything due."""
        if delta < 0:
            raise ValueError("delta must be >= 0")
        return self.run_until(self._now + int(delta))

    def run_until(self, target: int) -> int:
        """Advance to absolute tick ``target``, firing everything due."""
        target = int(target)
        if target < self._now:
            raise ValueError("cannot move the wheel backwards")
        while True:
            self._pump()
            if self._now >= target:
                break
            self._now = min(self._next_stop(), target)
        return self._now

    def pump(self) -> None:
        """Fire timers due at the current tick without advancing time."""
        self._pump()

    def sync(self) -> int:
        """Advance the wheel to the injected clock's current time."""
        return self.run_until(self._clock.time())

    def contains(self, node: TimerNode) -> bool:
        """True while ``node`` is still pending inside the wheel."""
        if node.cancelled or node.fired:
            return False
        if node in self._due:
            return True
        return any(node in slot for level in self._levels for slot in level)

    def pending(self) -> int:
        """Number of live (not cancelled, not fired) timers."""
        live = sum(1 for n in self._due if not n.cancelled)
        for level in self._levels:
            for slot in level:
                live += sum(1 for n in slot if not n.cancelled)
        return live

    # ------------------------------------------------------------- internals

    def _insert(self, node: TimerNode) -> None:
        remaining = node.expiry - self._now
        if remaining <= 0:
            self._due.append(node)
            node.placements.append(0)  # due bucket == current level-0 slot
            return
        level = 0
        span = self._size
        # level k holds remaining in [size**k, size**(k+1) - 1]; a timer exactly
        # one full round away must sit one level up, never in the current slot
        while remaining >= span:
            level += 1
            span <<= self._bits
        while level >= len(self._levels):
            self._levels.append([[] for _ in range(self._size)])
            self._non_empty.append(set())
        idx = (node.expiry >> (level * self._bits)) & self._mask
        self._levels[level][idx].append(node)
        self._non_empty[level].add(idx)
        node.placements.append(level)

    def _take_due(self) -> List[TimerNode]:
        if not self._due:
            return []
        group, self._due = self._due, []
        return group

    def _pump(self) -> None:
        # Cascade first (highest level down), then fire everything due now.
        for level in range(len(self._levels) - 1, 0, -1):
            if self._now % (self._size ** level) == 0:
                self._cascade(level)
        group = self._take_due()
        idx = self._now & self._mask
        slot = self._levels[0][idx]
        if slot:
            group.extend(slot)
            slot.clear()
            self._non_empty[0].discard(idx)
        if group:
            self._fire_group(group)

    def _fire_group(self, group: List[TimerNode]) -> None:
        group.sort(key=lambda n: n.seq)  # deterministic: insertion order
        for node in group:
            if node.cancelled or node.fired:
                continue
            node.fired += 1
            node.callback(node)

    def _cascade(self, level: int) -> None:
        idx = (self._now >> (level * self._bits)) & self._mask
        slot = self._levels[level][idx]
        if not slot:
            return
        entries, slot[:] = slot[:], []
        if not slot:
            self._non_empty[level].discard(idx)
        for node in entries:
            if node.cancelled:
                continue  # lazy cancel: physically dropped here
            self._insert(node)

    def _next_stop(self) -> int:
        """Next tick worth visiting: a level-0 slot or a cascade boundary."""
        best: Optional[int] = None
        for idx in self._non_empty[0]:
            base = self._now - (self._now & self._mask)
            t = base + idx
            if t <= self._now:
                t += self._size
            best = t if best is None else min(best, t)
        for level in range(1, len(self._levels)):
            if not self._non_empty[level]:
                continue
            period = self._size ** level
            t = (self._now // period + 1) * period
            best = t if best is None else min(best, t)
        if best is None:
            return self._now + 1
        return best
