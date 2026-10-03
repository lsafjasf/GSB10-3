"""Bulkhead-isolated thread pool (Python 3, standard library only).

Design
------
* A pool owns ``total_slots`` worker slots.
* Every registered category *reserves* ``reserved`` slots of that pool;
  the sum of all reservations must not exceed ``total_slots``.
* A category also gets its own bounded wait queue (``queue_limit``).
* Idle (unreserved / temporarily unused) slots may be *borrowed* by any
  other category that has demand beyond its reservation.
* Borrowed slots cannot be preempted from a running task.  When the owner
  category has queued demand and a slot frees, dispatch hands it back to
  the owner *before* lending it out again (reclaim priority).
* If a category cannot start a task immediately and its queue is already
  full, the incoming task is rejected: ``submit`` raises ``RejectedError``
  and the category's rejection counter is incremented (reject-the-newest
  policy).
* Task exceptions are captured on the returned Future and never leak a
  slot: the slot is released in a ``finally``-equivalent path.
"""

from __future__ import annotations

import threading
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass

__all__ = ["BulkheadPool", "Stats", "RejectedError"]


class RejectedError(RuntimeError):
    """Raised when a submission is rejected because the category queue is full."""


@dataclass(frozen=True)
class Stats:
    """Point-in-time statistics snapshot for one category."""

    running: int       # tasks currently executing
    queued: int        # tasks waiting in the category queue
    borrowed: int      # running tasks executing on borrowed slots
    completed: int     # tasks finished successfully
    failed: int        # tasks that raised an exception
    rejected: int      # submissions rejected because the queue was full


class _Task:
    __slots__ = ("fn", "args", "kwargs", "future")

    def __init__(self, fn, args, kwargs, future):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.future = future


class _Category:
    __slots__ = ("name", "reserved", "queue_limit", "queue",
                 "running", "borrowed", "completed", "failed", "rejected")

    def __init__(self, name, reserved, queue_limit):
        self.name = name
        self.reserved = reserved
        self.queue_limit = queue_limit
        self.queue = deque()
        self.running = 0
        self.borrowed = 0
        self.completed = 0
        self.failed = 0
        self.rejected = 0


class BulkheadPool:
    """Thread pool with per-category bulkhead isolation."""

    def __init__(self, total_slots):
        if not isinstance(total_slots, int) or total_slots <= 0:
            raise ValueError("total_slots must be a positive integer")
        self._total = total_slots
        self._free = total_slots
        self._cond = threading.Condition()
        self._categories = {}
        self._closed = False

    # ---- configuration ---------------------------------------------------

    @property
    def total_slots(self):
        return self._total

    def register_category(self, name, reserved, queue_limit):
        """Reserve ``reserved`` slots for a category with a bounded queue."""
        if not isinstance(reserved, int) or reserved < 0:
            raise ValueError("reserved must be a non-negative integer")
        if not isinstance(queue_limit, int) or queue_limit < 0:
            raise ValueError("queue_limit must be a non-negative integer")
        with self._cond:
            if self._closed:
                raise RuntimeError("pool is shut down")
            if name in self._categories:
                raise ValueError(f"category {name!r} already registered")
            used = sum(c.reserved for c in self._categories.values())
            if used + reserved > self._total:
                raise ValueError(
                    f"cannot reserve {reserved} slots: {used} of "
                    f"{self._total} already reserved"
                )
            self._categories[name] = _Category(name, reserved, queue_limit)

    # ---- submission ------------------------------------------------------

    def submit(self, category, fn, *args, **kwargs):
        """Submit ``fn`` for execution under ``category``.

        Returns a ``concurrent.futures.Future``.
        Raises ``RejectedError`` (and bumps the rejection counter) when the
        category cannot start immediately and its wait queue is full.
        """
        with self._cond:
            if self._closed:
                raise RuntimeError("pool is shut down")
            cat = self._categories.get(category)
            if cat is None:
                raise KeyError(f"unknown category {category!r}")

            future = Future()
            task = _Task(fn, args, kwargs, future)

            # Invariant: after every completion we dispatch, so whenever
            # self._free > 0 every queue is empty. Therefore a free slot
            # can start this task immediately, either as an owned slot or
            # as a borrow; otherwise it must queue or be rejected.
            if self._free > 0:
                self._start_locked(cat, task)
            elif len(cat.queue) < cat.queue_limit:
                cat.queue.append(task)
            else:
                cat.rejected += 1
                raise RejectedError(
                    f"category {category!r} rejected: queue limit "
                    f"{cat.queue_limit} reached"
                )
            return future

    # ---- introspection ---------------------------------------------------

    def stats(self, category):
        with self._cond:
            cat = self._categories[category]
            return Stats(
                running=cat.running,
                queued=len(cat.queue),
                borrowed=cat.borrowed,
                completed=cat.completed,
                failed=cat.failed,
                rejected=cat.rejected,
            )

    def free_slots(self):
        """Currently unoccupied pool slots (borrowable by anyone)."""
        with self._cond:
            return self._free

    def available_for(self, category):
        """Slots the category can currently claim as its own."""
        with self._cond:
            cat = self._categories[category]
            return max(0, min(cat.reserved - cat.running, self._free))

    # ---- lifecycle -------------------------------------------------------

    def shutdown(self, wait=True):
        """Reject new submissions; with wait=True drain all tasks."""
        with self._cond:
            self._closed = True
            if wait:
                while self._free < self._total:
                    self._cond.wait()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.shutdown(wait=True)
        return False

    # ---- internals -------------------------------------------------------

    def _start_locked(self, cat, task):
        # A task runs on a borrowed slot whenever the category already has
        # all of its reserved slots busy.
        borrowed = cat.running >= cat.reserved
        self._free -= 1
        cat.running += 1
        if borrowed:
            cat.borrowed += 1
        thread = threading.Thread(
            target=self._run, args=(cat, task, borrowed), daemon=True
        )
        thread.start()

    def _dispatch_locked(self):
        # Pass 1 (reclaim): categories whose reserved capacity is not yet
        # covered get freed slots first, in registration order.
        while self._free > 0:
            for cat in self._categories.values():
                if cat.queue and cat.running < cat.reserved:
                    self._start_locked(cat, cat.queue.popleft())
                    break
            else:
                break
        # Pass 2 (lend out idle capacity): any remaining free slot may be
        # borrowed by a category that has queued excess demand.
        while self._free > 0:
            for cat in self._categories.values():
                if cat.queue:
                    self._start_locked(cat, cat.queue.popleft())
                    break
            else:
                break

    def _run(self, cat, task, borrowed):
        ran = False
        failed = False
        try:
            if task.future.set_running_or_notify_cancel():
                ran = True
                try:
                    result = task.fn(*task.args, **task.kwargs)
                except BaseException as exc:
                    task.future.set_exception(exc)
                    failed = True
                else:
                    task.future.set_result(result)
        finally:
            with self._cond:
                if ran:
                    if failed:
                        cat.failed += 1
                    else:
                        cat.completed += 1
                cat.running -= 1
                if borrowed:
                    cat.borrowed -= 1
                self._free += 1
                self._dispatch_locked()
                self._cond.notify_all()
