"""Spin / backoff strategies with a hard cap and escalation to yield/block.

Pure standard library. All time-related effects (sleep / yield / block) and the
random source are injectable, so behavior is fully testable and reproducible.

A backoff policy is a stateful iterator. Each failed acquire attempt calls
``step()`` which returns ``(action, delay)``:

    ("sleep", d)  -> caller should sleep d time units, 0 <= d <= cap
    ("yield", None) -> cap/max_spins reached, caller should yield the CPU
    ("block", None) -> cap/max_spins reached, caller should block on the lock

Call ``reset()`` after a successful acquire.
"""

import random
import threading
import time

SLEEP = "sleep"
YIELD = "yield"
BLOCK = "block"


class BackoffPolicy:
    """Base class: enforces the delay cap and the escalation to yield/block."""

    def __init__(self, cap=1024.0, max_spins=16, escalate=BLOCK):
        if escalate not in (YIELD, BLOCK):
            raise ValueError("escalate must be YIELD or BLOCK")
        if cap <= 0:
            raise ValueError("cap must be positive")
        if max_spins < 1:
            raise ValueError("max_spins must be >= 1")
        self.cap = float(cap)
        self.max_spins = max_spins
        self.escalate = escalate
        self.spins = 0
        self.escalations = 0

    def _raw_delay(self, spin):
        raise NotImplementedError

    def reset(self):
        self.spins = 0
        self.escalations = 0

    def step(self):
        """Advance one failed attempt; return (action, delay)."""
        self.spins += 1
        if self.spins > self.max_spins:
            self.escalations += 1
            return self.escalate, None
        return SLEEP, min(self._raw_delay(self.spins), self.cap)


class FixedBackoff(BackoffPolicy):
    """Sleep the same fixed delay after every failed attempt."""

    def __init__(self, delay=1.0, **kw):
        super().__init__(**kw)
        self.delay = float(delay)

    def _raw_delay(self, spin):
        return self.delay


class ExponentialBackoff(BackoffPolicy):
    """Delay doubles each attempt: base, 2*base, 4*base, ... capped at cap."""

    def __init__(self, base=1.0, **kw):
        super().__init__(**kw)
        self.base = float(base)

    def _raw_delay(self, spin):
        return self.base * (2.0 ** (spin - 1))


class ExponentialJitterBackoff(ExponentialBackoff):
    """Full jitter: uniform random in [0, min(base * 2**(n-1), cap)]."""

    def __init__(self, base=1.0, rng=None, **kw):
        super().__init__(base, **kw)
        self.rng = rng if rng is not None else random.Random()

    def _raw_delay(self, spin):
        bound = min(self.base * (2.0 ** (spin - 1)), self.cap)
        return self.rng.uniform(0.0, bound)


class SpinLock:
    """A spin lock driven by an injectable BackoffPolicy.

    All side effects are injectable for testing:
      lock:   object with acquire(blocking=False) / release() (default threading.Lock)
      sleep:  called with the backoff delay on SLEEP
      yield_now: called on YIELD escalation
      block:  called on BLOCK escalation
    """

    def __init__(self, lock=None, sleep=None, yield_now=None, block=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._sleep = sleep if sleep is not None else time.sleep
        self._yield_now = yield_now if yield_now is not None else self._os_yield
        self._block = block if block is not None else lambda: time.sleep(0.001)
        self.attempts = 0
        self.sleeps = 0
        self.yields = 0
        self.blocks = 0

    @staticmethod
    def _os_yield():
        time.sleep(0)

    def acquire(self, policy):
        policy.reset()
        while True:
            self.attempts += 1
            if self._lock.acquire(blocking=False):
                return
            action, delay = policy.step()
            if action == SLEEP:
                self.sleeps += 1
                self._sleep(delay)
            elif action == YIELD:
                self.yields += 1
                self._yield_now()
            else:
                self.blocks += 1
                self._block()

    def release(self):
        self._lock.release()
