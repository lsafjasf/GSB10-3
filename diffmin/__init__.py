"""diffmin: differential testing with test-case minimization (stdlib only).

Public API:
    Outcome, Difference
    capture(impl, case)
    find_difference(impl_a, impl_b, case)
    minimize(differs, case)
    minimize_differential(impl_a, impl_b, case)
    RoundRecord, MinimizationResult
"""

from .core import (
    Difference,
    MinimizationResult,
    Outcome,
    RoundRecord,
    capture,
    find_difference,
    minimize,
    minimize_differential,
)

__all__ = [
    "Difference",
    "MinimizationResult",
    "Outcome",
    "RoundRecord",
    "capture",
    "find_difference",
    "minimize",
    "minimize_differential",
]
