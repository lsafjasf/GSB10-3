"""Injectable clock.

The simulator owns a :class:`VirtualClock` and only ever observes time that
was explicitly advanced by simulated RTTs. Nothing here reads the system
clock, which is what makes runs reproducible.
"""


class VirtualClock:
    """Monotonic, fully controlled clock measured in seconds."""

    def __init__(self, start=0.0):
        if start < 0.0:
            raise ValueError("start time must be non-negative")
        self._now = float(start)

    def now(self):
        return self._now

    def advance(self, seconds):
        if seconds < 0.0:
            raise ValueError("cannot move the clock backwards")
        self._now += float(seconds)
        return self._now
