"""Differential testing harness built only on the Python standard library.

Two implementations ("reference" and "candidate") are fed the same input.
Each execution is captured as an :class:`ExecutionOutcome` that records
whether it returned a value or raised an exception. Two outcomes are
considered different when their *signatures* differ, so that
``return 1`` differs from ``raise ValueError(...)`` while two equal
return values do not.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, List, Optional, Tuple


@dataclass
class ExecutionOutcome:
    """Result of running one implementation on one input."""

    kind: str
    value: Any = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def raised(self) -> bool:
        return self.kind == "error"

    def signature(self) -> Tuple[Any, ...]:
        if self.kind == "value":
            return ("value", _safely_hashable_repr(self.value))
        return ("error", self.error_type, self.error_message)

    def describe(self) -> str:
        if self.kind == "value":
            return "value={!r}".format(self.value)
        return "raise {}({})".format(self.error_type, self.error_message)


def _safely_hashable_repr(value: Any) -> str:
    """Stable, equality-faithful description of an arbitrary return value."""
    try:
        repr(value)
    except Exception:
        return "<unreprable {}>".format(type(value).__name__)
    return repr(value)


def _run_one(fn: Callable[[Any], Any], inp: Any) -> ExecutionOutcome:
    try:
        return ExecutionOutcome(kind="value", value=fn(inp))
    except Exception as exc:
        return ExecutionOutcome(
            kind="error",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


@dataclass
class CompareResult:
    """Result of comparing the two implementations on one input."""

    input: Any
    reference: ExecutionOutcome
    candidate: ExecutionOutcome
    different: bool

    def assertion_lines(self) -> List[str]:
        """Human-readable differential assertion, before/after style."""
        return [
            "input={!r}".format(self.input),
            "reference -> {}".format(self.reference.describe()),
            "candidate  -> {}".format(self.candidate.describe()),
            "different  = {}".format(self.different),
        ]

    def describe(self) -> str:
        return "\n".join(self.assertion_lines())


@dataclass
class DifferentialHarness:
    """Run a reference and a candidate implementation and detect divergences.

    Parameters
    ----------
    reference:
        The trusted implementation; takes one input argument.
    candidate:
        The implementation under test; takes the same input argument.
    """

    reference: Callable[[Any], Any]
    candidate: Callable[[Any], Any]
    eval_count: int = field(default=0, init=False)

    def execute(self, inp: Any) -> Tuple[ExecutionOutcome, ExecutionOutcome]:
        ref_out = _run_one(self.reference, inp)
        cand_out = _run_one(self.candidate, inp)
        self.eval_count += 1
        return ref_out, cand_out

    def differs(self, inp: Any) -> bool:
        ref_out, cand_out = self.execute(inp)
        return ref_out.signature() != cand_out.signature()

    def compare(self, inp: Any) -> CompareResult:
        ref_out, cand_out = self.execute(inp)
        return CompareResult(
            input=inp,
            reference=ref_out,
            candidate=cand_out,
            different=ref_out.signature() != cand_out.signature(),
        )

    def sweep(self, inputs: Iterable[Any]) -> List[CompareResult]:
        return [self.compare(inp) for inp in inputs]

    def discrepancies(self, inputs: Iterable[Any]) -> List[CompareResult]:
        return [result for result in self.sweep(inputs) if result.different]


def compare(
    reference: Callable[[Any], Any],
    candidate: Callable[[Any], Any],
    inp: Any,
) -> CompareResult:
    """Convenience wrapper for a one-shot differential comparison."""
    return DifferentialHarness(reference, candidate).compare(inp)
