"""Async singleflight: coalesce concurrent calls for the same key.

Semantics (documented contract):

- Coalescing: while a call for a key is in flight, other callers for the
  same key share its result; the real factory runs exactly once.
- Failure: if the shared call fails, the *same* exception object is
  delivered to every waiter. Failures are NOT cached and NOT reused --
  the next call after a failure triggers a fresh real call (retry).
- Expiry: a successful result is cached for ``ttl`` seconds (per group
  default or per call). After expiry the next call triggers a fresh real
  call. With ``ttl=None`` results are never cached (coalescing only).
- Cancellation: cancelling one waiter never cancels the shared real
  call; remaining waiters still receive the result.

Only the Python standard library is used.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Hashable, Optional, Tuple

__all__ = ["SingleFlight", "Group"]

Factory = Callable[[], Awaitable[Any]]


class Group:
    """A singleflight group. Calls are coalesced per key within a group."""

    def __init__(
        self,
        ttl: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        :param ttl: default seconds a successful result stays cached.
            ``None`` means "do not cache": only in-flight calls coalesce.
        :param clock: monotonic clock, injectable for tests.
        """
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive or None")
        self._default_ttl = ttl
        self._clock = clock
        # key -> running Task shared by all waiters of this key
        self._inflight: Dict[Hashable, "asyncio.Task[Any]"] = {}
        # key -> (value, expires_at)
        self._cache: Dict[Hashable, Tuple[Any, float]] = {}

    async def do(
        self,
        key: Hashable,
        factory: Factory,
        ttl: Optional[float] = None,
    ) -> Any:
        """Run ``factory`` for ``key``, coalescing concurrent callers.

        :param key: hashable identity of the logical operation.
        :param factory: zero-arg async callable performing the real work.
        :param ttl: per-call cache TTL override; falls back to the group
            default. ``None`` (with no group default) disables caching.
        :returns: the (possibly shared) result of the real call.
        :raises: whatever the real call raised; the same exception object
            is re-raised to every waiter of the failed call.
        """
        cached = self._cache.get(key)
        if cached is not None and self._clock() < cached[1]:
            return cached[0]

        task = self._inflight.get(key)
        if task is None:
            # Leader: run the real call in its own task so that a waiter
            # being cancelled never interrupts the shared work.
            task = asyncio.ensure_future(factory())
            self._inflight[key] = task
            effective_ttl = ttl if ttl is not None else self._default_ttl
            task.add_done_callback(
                lambda done: self._settle(key, done, effective_ttl)
            )
        return await asyncio.shield(task)

    def _settle(
        self,
        key: Hashable,
        task: "asyncio.Task[Any]",
        ttl: Optional[float],
    ) -> None:
        self._inflight.pop(key, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # Failures are deliberately not cached: the next call retries.
            return
        if ttl is not None:
            self._cache[key] = (task.result(), self._clock() + ttl)

    def invalidate(self, key: Hashable) -> None:
        """Drop the cached result for ``key`` (in-flight calls unaffected)."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Drop all cached results."""
        self._cache.clear()

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    @property
    def cached_count(self) -> int:
        return len(self._cache)


# Convenience alias.
SingleFlight = Group
