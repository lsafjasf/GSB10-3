"""Differential testing and test-case minimization core.

Only the Python standard library is used.

The minimizer combines:
  1. block deletion (ddmin-style: granularity 2, 4, 8, ... doubled when every
     block at that granularity fails, reset toward 2 on success);
  2. single-element deletion repeated until a fixpoint.

Every attempted reduction is recorded as a RoundRecord containing the
difference assertion *before* and *after* the round. A reduction is accepted
only when the candidate case still triggers the difference. After the search
reaches a fixpoint, 1-minimality is re-verified independently: the result is
reported as minimal only if deleting each surviving element kills the
difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

Case = Sequence[Any]
DiffPredicate = Callable[[List[Any]], bool]
Implementation = Callable[[Case], Any]


@dataclass(frozen=True)
class Outcome:
    """Observable result of running one implementation on one case.

    Exceptions are ordinary outcomes, so "raises vs. returns a value" counts
    as a difference too.
    """

    kind: str  # "value" or "exception"
    payload: str

    def __str__(self) -> str:
        return self.payload


@dataclass(frozen=True)
class Difference:
    case: Tuple[Any, ...]
    outcome_a: Outcome
    outcome_b: Outcome

    def __str__(self) -> str:
        return (
            f"case={list(self.case)!r}\n"
            f"  impl_a -> [{self.outcome_a.kind}] {self.outcome_a}\n"
            f"  impl_b -> [{self.outcome_b.kind}] {self.outcome_b}"
        )


def capture(impl: Implementation, case: Case) -> Outcome:
    """Run impl(case), turning a return value or an exception into an Outcome."""
    materialized = list(case)
    try:
        return Outcome("value", repr(impl(materialized)))
    except Exception as exc:  # every exception is an observable outcome
        return Outcome("exception", f"{type(exc).__name__}: {exc}")


def find_difference(
    impl_a: Implementation, impl_b: Implementation, case: Case
) -> Optional[Difference]:
    """Return a Difference if the two implementations disagree on case."""
    materialized = list(case)
    outcome_a = capture(impl_a, materialized)
    outcome_b = capture(impl_b, materialized)
    if outcome_a != outcome_b:
        return Difference(tuple(materialized), outcome_a, outcome_b)
    return None


@dataclass(frozen=True)
class RoundRecord:
    """One reduction attempt, with the before/after difference assertions.

    before_diff asserts the current case (the reduction's starting point)
    differs; after_diff asserts the candidate case produced by this round
    differs. A round is accepted exactly when after_diff is True.
    """

    iteration: int
    strategy: str  # "block" or "single"
    detail: str
    before_size: int
    after_size: int
    before_diff: bool
    after_diff: bool
    accepted: bool


@dataclass
class MinimizationResult:
    original: List[Any]
    minimized: List[Any]
    rounds: List[RoundRecord] = field(default_factory=list)
    difference_checks: int = 0
    is_minimal_verified: bool = False

    @property
    def initial_size(self) -> int:
        return len(self.original)

    @property
    def final_size(self) -> int:
        return len(self.minimized)

    @property
    def iterations(self) -> int:
        return len(self.rounds)

    def accepted_rounds(self) -> List[RoundRecord]:
        return [r for r in self.rounds if r.accepted]

    def rejected_rounds(self) -> List[RoundRecord]:
        return [r for r in self.rounds if not r.accepted]

    def to_dict(self) -> dict:
        return {
            "initial_size": self.initial_size,
            "final_size": self.final_size,
            "difference_checks": self.difference_checks,
            "total_iterations": self.iterations,
            "accepted_iterations": len(self.accepted_rounds()),
            "rejected_iterations": len(self.rejected_rounds()),
            "is_minimal_verified": self.is_minimal_verified,
            "original": self.original,
            "minimized": self.minimized,
            "rounds": [
                {
                    "iteration": r.iteration,
                    "strategy": r.strategy,
                    "detail": r.detail,
                    "before_size": r.before_size,
                    "after_size": r.after_size,
                    "before_diff": r.before_diff,
                    "after_diff": r.after_diff,
                    "accepted": r.accepted,
                }
                for r in self.rounds
            ],
        }


def minimize(differs: DiffPredicate, case: Case) -> MinimizationResult:
    """Minimize a differing case under the predicate ``differs``.

    ``differs(candidate)`` must be True exactly when the candidate case still
    exhibits the difference between the two implementations.

    Raises ValueError if the original case does not differ: there is nothing
    to minimize, and reporting such input as a "minimal failure" would be
    unverified.
    """
    current = list(case)
    result = MinimizationResult(original=list(current), minimized=list(current))
    checks = 0

    def check(candidate: List[Any]) -> bool:
        nonlocal checks
        checks += 1
        return bool(differs(list(candidate)))

    def record(strategy: str, detail: str, candidate: List[Any], still_differs: bool) -> None:
        result.rounds.append(
            RoundRecord(
                iteration=len(result.rounds) + 1,
                strategy=strategy,
                detail=detail,
                before_size=len(current),
                after_size=len(candidate),
                before_diff=True,  # invariant: current itself differs
                after_diff=still_differs,
                accepted=still_differs,
            )
        )

    # The input itself must exhibit the difference.
    if not check(current):
        raise ValueError("original case does not exhibit any difference; cannot minimize")

    # ---- Phase 1: block deletion (ddmin with doubling granularity) ----
    granularity = 2
    while len(current) >= 2:
        block_size = max(1, len(current) // granularity)
        reduced_this_granularity = False
        start = 0
        while start < len(current):
            end = min(start + block_size, len(current))
            candidate = current[:start] + current[end:]
            still_differs = check(candidate)
            record(
                "block",
                f"granularity={granularity} delete indices [{start}:{end})",
                candidate,
                still_differs,
            )
            if still_differs:
                current = candidate
                granularity = max(2, granularity - 1)
                reduced_this_granularity = True
                break
            start = end
        if not reduced_this_granularity:
            if granularity >= len(current):
                break  # block size 1 already attempted here; phase 2 finalizes
            granularity = min(granularity * 2, len(current))

    # ---- Phase 2: single-element deletion repeated to a fixpoint ----
    index = 0
    while index < len(current):
        candidate = current[:index] + current[index + 1 :]
        still_differs = check(candidate)
        record("single", f"delete index {index}", candidate, still_differs)
        if still_differs:
            current = candidate
            index = 0  # restart the sweep from the beginning
        else:
            index += 1

    # ---- Independent verification of 1-minimality ----
    # Do not trust the search itself: try deleting each surviving element one
    # more time, from scratch. Claim "minimal" only if every such deletion
    # kills the difference.
    verified = True
    for index in range(len(current)):
        checks += 1
        if differs(current[:index] + current[index + 1 :]):
            verified = False
            break

    result.minimized = current
    result.difference_checks = checks
    result.is_minimal_verified = verified
    return result


def minimize_differential(
    impl_a: Implementation, impl_b: Implementation, case: Case
) -> Tuple[MinimizationResult, Difference]:
    """Minimize a raw case by comparing two implementations.

    Returns (result, final_difference), where final_difference is the
    concrete Difference observed on the minimized case.
    """
    def differs(candidate: List[Any]) -> bool:
        return find_difference(impl_a, impl_b, candidate) is not None

    result = minimize(differs, list(case))
    final_difference = find_difference(impl_a, impl_b, result.minimized)
    if final_difference is None:
        raise RuntimeError("internal error: minimized case no longer differs")
    return result, final_difference
