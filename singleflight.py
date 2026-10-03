"""Async singleflight: deduplicate concurrent calls on the same key.

Semantics (important):
- Concurrent merge: while a call for a key is in flight, all other
  callers for the same key share the same underlying task; the real
  call happens exactly once.
- Failure fan-out: an exception raised by the underlying call is
  delivered as-is to every waiter (the same exception instance).
- Failure is NOT reused: failures are never cached. The in-flight
  entry is removed when the call finishes, so the next request after
  a failure triggers a fresh real call (automatic retry semantics)
  instead of replaying the cached error.
- Expiry: only when ttl (seconds) is given, successful results are
  cached for ttl seconds; after expiry the next request performs a
  real call again. ttl=None disables caching entirely.
"""

import asyncio
import time

__all__ = ["SingleFlight"]


class SingleFlight:
    def __init__(self, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive or None")
        self._ttl = ttl
        self._inflight = {}  # key -> asyncio.Task (real call in progress)
        self._cache = {}     # key -> (expires_at, value), ttl mode only

    async def do(self, key, fn):
        """Run fn (zero-arg callable returning an awaitable) for key.

        Concurrent callers on the same key share one real call.
        Raises whatever fn raises, to all waiters of that key.
        """
        if self._ttl is not None:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > time.monotonic():
                return cached[1]

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(fn())
            self._inflight[key] = task
            task.add_done_callback(lambda t: self._inflight.pop(key, None))

        # shield: if one waiter is cancelled, the shared underlying
        # task keeps running and the remaining waiters still get
        # the result.
        result = await asyncio.shield(task)

        if self._ttl is not None:
            self._cache[key] = (time.monotonic() + self._ttl, result)
        return result
