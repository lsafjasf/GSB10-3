"""Adaptive batch sizing for message consumers (stdlib only).

Strategy: AIMD (additive increase / multiplicative decrease) with
  * EMA smoothing of batch latency and failure rate (noise rejection),
  * a deadband around the target latency (no adjust when "good enough"),
  * an increase cooldown after every decrease (kills oscillation),
  * hard min/max bounds.

Time is injectable: pass `now=<callable>` (defaults to time.monotonic).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class AdjustEvent:
    """One adjustment decision, recorded for observability."""
    seq: int
    old_size: int
    new_size: int
    reason: str            # 'slowdown' | 'failures' | 'speedup' | 'hold'
    latency_s: float       # raw observed batch latency
    lat_ema: float         # smoothed latency
    fail_ema: float        # smoothed failure rate
    at: float              # injected clock reading


class AdaptiveBatchSizer:
    def __init__(
        self,
        min_size: int = 10,
        max_size: int = 500,
        initial_size: Optional[int] = None,
        target_latency: float = 0.20,     # seconds per batch we aim for
        deadband: float = 0.15,           # +-15% around target => hold
        increase_rate: float = 0.1,       # additive increase: +10% of current size (slow)
        decrease_factor: float = 0.5,     # fastest multiplicative decrease per batch (fast)
        panic_factor: float = 0.25,       # extra-aggressive cut when latency > panic_ratio * target
        panic_ratio: float = 3.0,
        max_fail_rate: float = 0.05,      # smoothed failure rate ceiling
        ema_alpha: float = 0.4,           # smoothing weight of newest sample
        cooldown_batches: int = 3,        # batches to suppress increases after a decrease
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (1 <= min_size <= max_size):
            raise ValueError("require 1 <= min_size <= max_size")
        if target_latency <= 0:
            raise ValueError("target_latency must be > 0")
        if not (0.0 < ema_alpha <= 1.0):
            raise ValueError("ema_alpha must be in (0, 1]")
        if not (0.0 < decrease_factor < 1.0) or not (0.0 < panic_factor < 1.0):
            raise ValueError("decrease factors must be in (0, 1)")
        if not (0.0 < increase_rate <= 1.0):
            raise ValueError("increase_rate must be in (0, 1]")

        self.min_size = min_size
        self.max_size = max_size
        self.target_latency = target_latency
        self.deadband = deadband
        self.increase_rate = increase_rate
        self.decrease_factor = decrease_factor
        self.panic_factor = panic_factor
        self.panic_ratio = panic_ratio
        self.max_fail_rate = max_fail_rate
        self.ema_alpha = ema_alpha
        self.cooldown_batches = cooldown_batches
        self._now = now

        init = initial_size if initial_size is not None else (min_size + max_size) // 2
        self._size = self._clamp(init)
        self._lat_ema: Optional[float] = None
        self._fail_ema: float = 0.0
        self._cooldown_left = 0
        self._seq = 0
        self.events: List[AdjustEvent] = []

    # ------------------------------------------------------------------ API
    @property
    def batch_size(self) -> int:
        return self._size

    def record(self, latency_s: float, failures: int = 0, batch_size: Optional[int] = None) -> int:
        """Feed back the outcome of the batch just processed; returns new size."""
        if latency_s < 0:
            raise ValueError("latency_s must be >= 0")
        n = self._size if batch_size is None else batch_size
        if n <= 0:
            raise ValueError("batch_size must be > 0")
        if not (0 <= failures <= n):
            raise ValueError("failures must be within [0, batch_size]")

        a = self.ema_alpha
        self._lat_ema = latency_s if self._lat_ema is None else a * latency_s + (1 - a) * self._lat_ema
        self._fail_ema = a * (failures / n) + (1 - a) * self._fail_ema

        old = self._size
        reason = "hold"
        new = old

        lat = self._lat_ema
        hi = self.target_latency * (1.0 + self.deadband)
        lo = self.target_latency * (1.0 - self.deadband)

        if self._fail_ema > self.max_fail_rate:
            # Failures dominate: shrink fast, retries are expensive.
            new = int(old * self.decrease_factor)
            reason = "failures"
        elif lat >= self.target_latency * self.panic_ratio:
            # Sudden severe slowdown: cut hard in one step.
            new = int(old * self.panic_factor)
            reason = "slowdown"
        elif lat > hi:
            # Proportional cut sized by the *raw* latency (EMA lags and would
            # overshoot); bounded so one batch never cuts deeper than decrease_factor.
            factor = self.target_latency / latency_s if latency_s > 0 else 1.0
            factor = max(self.decrease_factor, min(1.0, factor))
            new = int(old * factor)
            reason = "slowdown"
        elif lat < lo and self._fail_ema <= self.max_fail_rate * 0.5:
            if self._cooldown_left > 0:
                reason = "hold"  # cooldown: don't bounce right back up
            else:
                new = old + max(1, int(old * self.increase_rate))
                reason = "speedup"

        new = self._clamp(new)
        if new < old:
            self._cooldown_left = self.cooldown_batches
        elif self._cooldown_left > 0:
            self._cooldown_left -= 1

        if new == old and reason in ("slowdown", "failures", "speedup"):
            reason = "hold"  # clamped at a bound; nothing actually changed

        self._size = new
        self._seq += 1
        self.events.append(AdjustEvent(self._seq, old, new, reason,
                                       latency_s, lat, self._fail_ema, self._now()))
        return new

    # ------------------------------------------------------------- helpers
    def _clamp(self, size: int) -> int:
        return max(self.min_size, min(self.max_size, int(size)))

    def stats(self):
        """(consecutive_adjust_streak_max, events) for stability analysis."""
        streak = best = 0
        prev_sign = 0
        for e in self.events:
            d = e.new_size - e.old_size
            sign = (d > 0) - (d < 0)
            if sign != 0:
                streak = streak + 1 if sign == prev_sign else 1
                best = max(best, streak)
                prev_sign = sign
        return best, self.events
