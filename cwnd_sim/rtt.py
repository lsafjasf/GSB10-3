"""Round-trip-time profiles. RTT is a pure function of flight index."""

from typing import List, Tuple


class RttProfile:
    """Interface: RTT in milliseconds for the given flight index."""

    def rtt_ms(self, flight_index: int) -> float:
        raise NotImplementedError


class ConstantRtt(RttProfile):
    def __init__(self, rtt_ms: float) -> None:
        if rtt_ms <= 0:
            raise ValueError("rtt_ms must be positive")
        self._rtt = float(rtt_ms)

    def rtt_ms(self, flight_index: int) -> float:
        return self._rtt


class StepRtt(RttProfile):
    """RTT changes at fixed flight indices, e.g. to model a latency spike.

    steps is a list of (from_flight_index, rtt_ms) sorted by index; the
    first entry applies from flight 0.
    """

    def __init__(self, steps: List[Tuple[int, float]]) -> None:
        if not steps or steps[0][0] != 0:
            raise ValueError("steps must start at flight 0")
        for _, rtt in steps:
            if rtt <= 0:
                raise ValueError("rtt must be positive")
        self._steps = list(steps)

    def rtt_ms(self, flight_index: int) -> float:
        current = self._steps[0][1]
        for start, rtt in self._steps:
            if flight_index >= start:
                current = rtt
            else:
                break
        return float(current)
