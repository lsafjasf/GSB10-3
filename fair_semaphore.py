"""A strictly FIFO, cancellation-aware semaphore using only the stdlib."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional


class FairSemaphoreTimeout(TimeoutError):
    """Raised when a timed acquire expires before a permit is granted."""


class FairSemaphoreCancelled(Exception):
    """Raised when a waiting acquire is cancelled by its cancellation event."""


@dataclass
class _Waiter:
    ticket: int
    label: str
    granted: bool = False
    removed: bool = False


class FairSemaphore:
    """A bounded semaphore whose permits are handed off in arrival order.

    A queued permit is reserved for the queue head. Later arrivals cannot
    barge ahead of it, even if they call acquire before the head wakes up.
    """

    def __init__(self, value: int = 1) -> None:
        if value < 0:
            raise ValueError("semaphore value must be non-negative")
        self._available = value
        self._held = 0
        self._next_ticket = 0
        self._queue: deque[_Waiter] = deque()
        self._condition = threading.Condition()

    def acquire(
        self,
        timeout: Optional[float] = None,
        *,
        cancel_event: Optional[threading.Event] = None,
        label: Optional[str] = None,
        cancel_poll_interval: float = 0.01,
    ) -> None:
        """Acquire a permit, waiting in FIFO order when none is available.

        Args:
            timeout: Maximum seconds to wait. None waits indefinitely.
            cancel_event: Optional event that cancels this wait when set.
            label: Human-readable name exposed by queue_snapshot().
            cancel_poll_interval: Maximum delay before cancellation is seen.

        Raises:
            FairSemaphoreTimeout: The timeout expired before a grant.
            FairSemaphoreCancelled: cancel_event was set before acquisition.
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        if cancel_poll_interval <= 0:
            raise ValueError("cancel_poll_interval must be positive")

        waiter_label = label or threading.current_thread().name
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._condition:
            if cancel_event is not None and cancel_event.is_set():
                raise FairSemaphoreCancelled("acquire was cancelled")

            if not self._queue and self._available > 0:
                self._available -= 1
                self._held += 1
                return

            if timeout == 0:
                raise FairSemaphoreTimeout("acquire timed out")

            waiter = _Waiter(
                ticket=self._next_ticket,
                label=waiter_label,
            )
            self._next_ticket += 1
            self._queue.append(waiter)

            while True:
                if waiter.granted:
                    if cancel_event is not None and cancel_event.is_set():
                        self._remove_waiter_locked(waiter)
                        self._available += 1
                        self._handoff_locked()
                        raise FairSemaphoreCancelled("acquire was cancelled")

                    self._remove_waiter_locked(waiter)
                    self._held += 1
                    self._handoff_locked()
                    return

                if cancel_event is not None and cancel_event.is_set():
                    self._remove_waiter_locked(waiter)
                    self._handoff_locked()
                    raise FairSemaphoreCancelled("acquire was cancelled")

                if deadline is None:
                    wait_time = (
                        cancel_poll_interval
                        if cancel_event is not None
                        else None
                    )
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._remove_waiter_locked(waiter)
                        self._handoff_locked()
                        raise FairSemaphoreTimeout("acquire timed out")
                    wait_time = (
                        min(cancel_poll_interval, remaining)
                        if cancel_event is not None
                        else remaining
                    )

                self._condition.wait(wait_time)

    def release(self) -> None:
        """Release one held permit, reserving it for the queue head if any."""
        with self._condition:
            if self._held <= 0:
                raise RuntimeError("release called without a held permit")
            self._held -= 1
            self._available += 1
            self._handoff_locked()

    def queue_snapshot(self) -> list[dict[str, object]]:
        """Return queued waiters in FIFO order without mutating the queue."""
        with self._condition:
            return [
                {
                    "ticket": waiter.ticket,
                    "label": waiter.label,
                    "state": "granted" if waiter.granted else "waiting",
                }
                for waiter in self._queue
                if not waiter.removed
            ]

    def waiting_count(self) -> int:
        """Return the number of waiters currently visible in the queue."""
        with self._condition:
            return sum(not waiter.removed for waiter in self._queue)

    def _handoff_locked(self) -> None:
        """Reserve one available permit for the current queue head."""
        if self._available <= 0 or not self._queue:
            return

        head = self._queue[0]
        if head.removed or head.granted:
            return

        head.granted = True
        self._available -= 1
        self._condition.notify_all()

    def _remove_waiter_locked(self, waiter: _Waiter) -> None:
        waiter.removed = True
        try:
            self._queue.remove(waiter)
        except ValueError:
            pass
