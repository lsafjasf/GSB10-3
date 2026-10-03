"""Loss models. A loss model decides, per flight, which packets are lost.

A flight is one window's worth of packets sent back to back; packets are
identified by (flight_index, position_in_flight). Decisions must be pure
functions of those identifiers plus the model's own fixed parameters, so
results never depend on wall-clock time.
"""

from __future__ import annotations

import random
from typing import Dict, Iterable, List, Tuple


class LossModel:
    """Interface: return sorted positions of lost packets within a flight."""

    def lost_positions(self, flight_index: int, flight_size: int) -> List[int]:
        raise NotImplementedError


class NoLoss(LossModel):
    def lost_positions(self, flight_index: int, flight_size: int) -> List[int]:
        return []


class ScriptLoss(LossModel):
    """Fully scripted loss pattern.

    spec maps flight_index -> iterable of positions lost in that flight.
    Example: {3: [0]} loses the first packet of flight 3 only.
    """

    def __init__(self, spec: Dict[int, Iterable[int]]) -> None:
        self._spec = {k: sorted(set(v)) for k, v in spec.items()}

    def lost_positions(self, flight_index: int, flight_size: int) -> List[int]:
        return [p for p in self._spec.get(flight_index, []) if p < flight_size]


class RandomLoss(LossModel):
    """Seeded random loss. Deterministic for a fixed seed."""

    def __init__(self, seed: int, loss_rate: float) -> None:
        if not 0.0 <= loss_rate <= 1.0:
            raise ValueError("loss_rate must be in [0, 1]")
        self._rng = random.Random(seed)
        self._loss_rate = loss_rate

    def lost_positions(self, flight_index: int, flight_size: int) -> List[int]:
        return [p for p in range(flight_size) if self._rng.random() < self._loss_rate]
