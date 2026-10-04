"""Delta-debugging minimizer that preserves a differential divergence.

The minimizer combines two deletion strategies, in the classic ddmin style:

* block deletion -- try to remove contiguous chunks of the input, first
  coarse (half, third, ...) then fine;
* single-element deletion -- every accepted reduction is followed by a
  singleton sweep that tries to delete each remaining element on its own,
  and a final sweep certifies that the result is 1-minimal.

Every accepted reduction is recorded as a :class:`ReductionRound` together
with the differential assertion *before* and *after* the reduction, so the
report itself proves that the divergence survived every step.

Honesty contract: :func:`minimize` only returns after the final result has
been re-verified against the harness (divergence still present, and no
single element can be removed without losing it). If verification fails --
for example because the candidate implementation is flaky -- a
:class:`MinimizationError` is raised instead of returning an unverified
result.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, List, Optional, Sequence

from .harness import CompareResult, DifferentialHarness


class MinimizationError(RuntimeError):
    """Raised when a minimization result cannot be honestly verified."""


class NoDifferenceError(ValueError):
    """Raised when the input shows no divergence, so there is nothing to
    minimize."""


def _split_chunks(seq: Sequence[Any], n: int) -> List[Sequence[Any]]:
    """Split ``seq`` into ``n`` contiguous chunks, sizes differing by <= 1."""
    length = len(seq)
    if n <= 0:
        raise ValueError("n must be positive")
    base, extra = divmod(length, n)
    chunks = []
    start = 0
    for index in range(n):
        size = base + (1 if index < extra else 0)
        chunks.append(seq[start:start + size])
        start += size
    return [chunk for chunk in chunks if len(chunk) > 0]


@dataclass
class ReductionRound:
    """One accepted reduction, with before/after differential assertions."""

    index: int
    phase: str
    granularity: int
    removed_count: int
    before: CompareResult
    after: CompareResult

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "phase": self.phase,
            "granularity": self.granularity,
            "removed_count": self.removed_count,
            "before_size": len(self.before.input),
            "after_size": len(self.after.input),
            "assertion": {
                "before": self.before.assertion_lines(),
                "after": self.after.assertion_lines(),
            },
        }


@dataclass
class SingletonCheck:
    """One attempt to delete a single element during the final sweep."""

    index: int
    element: Any
    kept: bool
    detail: str


@dataclass
class MinimizationReport:
    """Full, verifiable record of a minimization run."""

    original: List[Any]
    result: List[Any]
    rounds: List[ReductionRound]
    trials: int
    iterations: int
    singleton_checks: List[SingletonCheck]
    status: str
    verified: bool
    final_assertion: CompareResult

    @property
    def original_size(self) -> int:
        return len(self.original)

    @property
    def final_size(self) -> int:
        return len(self.result)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "verified": self.verified,
            "original_size": self.original_size,
            "final_size": self.final_size,
            "iterations": self.iterations,
            "trials": self.trials,
            "original": list(self.original),
            "result": list(self.result),
            "rounds": [rnd.to_dict() for rnd in self.rounds],
            "singleton_checks": [asdict(check) for check in self.singleton_checks],
            "final_assertion": self.final_assertion.assertion_lines(),
        }

    def to_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("indent", 2)
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.to_dict(), **kwargs)

    def verify(self, harness: DifferentialHarness) -> bool:
        """Re-check every recorded claim against ``harness``.

        Raises :class:`MinimizationError` if any recorded round or the
        final result fails to reproduce the divergence.
        """
        for rnd in self.rounds:
            if not harness.differs(list(rnd.before.input)):
                raise MinimizationError(
                    "round {} 'before' no longer diverges".format(rnd.index)
                )
            if not harness.differs(list(rnd.after.input)):
                raise MinimizationError(
                    "round {} 'after' no longer diverges".format(rnd.index)
                )
        if not harness.differs(list(self.result)):
            raise MinimizationError("final result no longer diverges")
        for index in range(len(self.result)):
            reduced = list(self.result[:index]) + list(self.result[index + 1:])
            if harness.differs(reduced):
                raise MinimizationError(
                    "final result is not 1-minimal: element at index {} "
                    "can still be removed".format(index)
                )
        return True


def minimize(
    harness: DifferentialHarness,
    original: Sequence[Any],
    verify: bool = True,
) -> MinimizationReport:
    """Minimize ``original`` while preserving the divergence.

    Parameters
    ----------
    harness:
        The differential harness whose ``differs`` predicate defines the
        property to preserve.
    original:
        The failing input (any sequence of elements).
    verify:
        When true (default), re-verify the final result and raise
        :class:`MinimizationError` instead of returning anything
        unverified.

    Returns
    -------
    MinimizationReport
        ``status`` is always ``"minimal"`` for a returned report; a result
        that cannot be verified is never returned.
    """
    original = list(original)
    trials = 0

    def differs(candidate: Sequence[Any]) -> bool:
        nonlocal trials
        trials += 1
        return harness.differs(list(candidate))

    initial = harness.compare(list(original))
    if not initial.different:
        raise NoDifferenceError(
            "input of size {} shows no divergence; nothing to minimize".format(
                len(original)
            )
        )

    current = list(original)
    rounds: List[ReductionRound] = []
    iterations = 0

    if len(current) >= 2:
        n = 2
        while len(current) >= 2:
            chunks = _split_chunks(current, n)
            reduced_this_pass = False

            for chunk in chunks:
                trial = [e for e in current if e not in chunk]
                if len(trial) == len(current):
                    continue
                if differs(trial):
                    before = harness.compare(list(current))
                    after = harness.compare(list(trial))
                    iterations += 1
                    rounds.append(
                        ReductionRound(
                            index=len(rounds) + 1,
                            phase="block-delete",
                            granularity=n,
                            removed_count=len(current) - len(trial),
                            before=before,
                            after=after,
                        )
                    )
                    current = trial
                    n = max(n - 1, 2)
                    reduced_this_pass = True
                    break

            if reduced_this_pass:
                continue

            for chunk in chunks:
                complement = [e for e in current if e in chunk]
                if not complement or len(complement) == len(current):
                    continue
                if differs(complement):
                    before = harness.compare(list(current))
                    after = harness.compare(list(complement))
                    iterations += 1
                    rounds.append(
                        ReductionRound(
                            index=len(rounds) + 1,
                            phase="block-keep",
                            granularity=n,
                            removed_count=len(current) - len(complement),
                            before=before,
                            after=after,
                        )
                    )
                    current = complement
                    n = 2
                    reduced_this_pass = True
                    break

            if reduced_this_pass:
                continue

            if n >= len(current):
                break
            n = min(len(current), n * 2)

    singleton_checks: List[SingletonCheck] = []
    changed = True
    while changed:
        changed = False
        index = 0
        while index < len(current):
            trial = current[:index] + current[index + 1:]
            if differs(trial):
                before = harness.compare(list(current))
                after = harness.compare(list(trial))
                iterations += 1
                rounds.append(
                    ReductionRound(
                        index=len(rounds) + 1,
                        phase="singleton-delete",
                        granularity=1,
                        removed_count=1,
                        before=before,
                        after=after,
                    )
                )
                current = trial
                changed = True
            else:
                index += 1

    final_assertion = harness.compare(list(current))
    if not final_assertion.different:
        raise MinimizationError(
            "internal error: minimization lost the divergence"
        )

    for index in range(len(current)):
        trial = current[:index] + current[index + 1:]
        trials += 1
        kept = not harness.differs(list(trial))
        singleton_checks.append(
            SingletonCheck(
                index=index,
                element=current[index],
                kept=kept,
                detail=(
                    "removing {!r} loses the divergence".format(current[index])
                    if kept
                    else "removing {!r} still diverges".format(current[index])
                ),
            )
        )
        if not kept:
            raise MinimizationError(
                "result is not 1-minimal: element {!r} at index {} can "
                "still be removed".format(current[index], index)
            )

    report = MinimizationReport(
        original=list(original),
        result=list(current),
        rounds=rounds,
        trials=trials,
        iterations=iterations,
        singleton_checks=singleton_checks,
        status="minimal",
        verified=False,
        final_assertion=final_assertion,
    )

    if verify:
        report.verify(harness)
        report.verified = True

    return report
