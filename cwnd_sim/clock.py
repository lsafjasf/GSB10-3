"""Injectable manual clock. The simulator never touches the real clock."""


class ManualClock:
    """Monotonic clock advanced only by explicit calls to advance()."""

    def __init__(self, start_ms: float = 0.0) -> None:
        self._now = float(start_ms)

    def now(self) -> float:
        return self._now

    def advance(self, delta_ms: float) -> float:
        if delta_ms < 0:
            raise ValueError("cannot move clock backwards")
        self._now += float(delta_ms)
        return self._now
