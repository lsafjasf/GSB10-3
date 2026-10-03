"""Fair (strict FIFO) semaphore -- Python 3, standard library only.

Guarantees
----------
1. Waiters are enqueued in arrival order and are woken strictly in that
   order (FIFO).
2. ``release()`` hands the permit *directly* to the head-of-queue waiter.
   The permit never becomes visible to newly arriving ``acquire()`` calls
   while a queue exists, so a fresh arrival can never steal a wakeup.
3. A wait that times out (or is cancelled) removes itself from the queue;
   the relative order of all remaining waiters is preserved.
4. Cancellation is cooperative: the caller passes a ``threading.Event``
   via ``cancel=``; when the event is set the waiter aborts promptly
   (within ``_CANCEL_POLL`` seconds) and dequeues itself.

If a permit was *already* handed to a waiter that then aborts (timeout /
cancel racing with ``release``), the permit is re-handed to the next
waiter in line -- permits are never leaked.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

_CANCEL_POLL = 0.02  # max latency (s) for noticing a cancel event


@dataclass
class _Waiter:
    seq: int  # arrival sequence number (FIFO ticket)
    name: str
    event: threading.Event = field(default_factory=threading.Event)
    queued: bool = True  # still present in the queue
    granted: bool = False  # a permit has been handed to this waiter


class FairSemaphore:
    """A semaphore whose waiters are served in strict FIFO order."""

    def __init__(self, value: int = 1):
        if value < 0:
            raise ValueError("semaphore initial value must be >= 0")
        self._value = value
        self._initial = value
        self._cond = threading.Condition()
        self._queue: Deque[_Waiter] = deque()
        self._seq = 0

    # ------------------------------------------------------------------ #
    # acquire
    # ------------------------------------------------------------------ #
    def acquire(
        self,
        timeout: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
        name: Optional[str] = None,
    ) -> bool:
        """Acquire one permit.  Returns True on success, False on
        timeout / cancellation.

        ``timeout``  -- seconds (None = wait forever).
        ``cancel``   -- optional threading.Event; when set the wait aborts.
        ``name``     -- label used in ``snapshot()`` (defaults to waiter-N).
        """
        with self._cond:
            if not self._queue and self._value > 0:
                # Fast path: nobody waiting -> take a permit immediately.
                self._value -= 1
                return True
            # Slow path: take a FIFO ticket and join the queue.
            self._seq += 1
            waiter = _Waiter(seq=self._seq,
                             name=name or "waiter-%d" % self._seq)
            self._queue.append(waiter)

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                self._abort(waiter)
                return False
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._abort(waiter)
                    return False
                if cancel is not None:
                    slice_ = min(remaining, _CANCEL_POLL)
                else:
                    slice_ = remaining
            else:
                slice_ = _CANCEL_POLL if cancel is not None else None
            waiter.event.wait(slice_)
            if waiter.event.is_set():
                return True

    # ------------------------------------------------------------------ #
    # release
    # ------------------------------------------------------------------ #
    def release(self) -> None:
        """Release one permit.

        If waiters are queued the permit is handed *directly* to the head
        of the queue; it is never exposed to new arrivals.
        """
        with self._cond:
            self._handoff_locked()

    def _handoff_locked(self) -> None:
        while self._queue:
            head = self._queue[0]
            if not head.queued:  # stale entry (defensive)
                self._queue.popleft()
                continue
            head.queued = False
            self._queue.popleft()
            head.granted = True
            head.event.set()  # direct handoff to the head
            return
        # Nobody waiting: the permit goes back to the counter.
        if self._value >= self._initial:
            raise ValueError("release() called too many times")
        self._value += 1

    # ------------------------------------------------------------------ #
    # abort (timeout / cancel): dequeue, keep others' relative order
    # ------------------------------------------------------------------ #
    def _abort(self, waiter: _Waiter) -> None:
        with self._cond:
            if waiter.queued:
                waiter.queued = False
                try:
                    self._queue.remove(waiter)
                except ValueError:
                    pass
            elif waiter.granted:
                # We were handed a permit but never consumed it:
                # pass it on to the next waiter (or back to the counter).
                waiter.granted = False
                self._handoff_locked()

    # ------------------------------------------------------------------ #
    # introspection helpers (used by tests / demos)
    # ------------------------------------------------------------------ #
    def snapshot(self) -> List[str]:
        """Names of the queued waiters, head first, in wakeup order."""
        with self._cond:
            return [w.name for w in self._queue if w.queued]

    @property
    def value(self) -> int:
        with self._cond:
            return self._value

    def __len__(self) -> int:
        with self._cond:
            return len(self._queue)
