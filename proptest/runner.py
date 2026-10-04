"""Property runner: generate inputs, shrink failures, stay reproducible."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .gen import Gen, Tree


@dataclass(frozen=True)
class CounterExample:
    """The first failing input and its shrunk minimal counterpart."""

    original: Any
    minimal: Any
    shrink_steps: int


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    seed: int
    runs: int
    counterexample: Optional[CounterExample] = None
    error: Optional[BaseException] = None

    def assert_ok(self) -> None:
        if not self.ok:
            ce = self.counterexample
            raise AssertionError(
                f"property failed (seed={self.seed}, after {self.runs} run(s))\n"
                f"  before shrinking: {ce.original!r}\n"
                f"  minimal example : {ce.minimal!r}\n"
                f"  shrink steps    : {ce.shrink_steps}"
            )


def _failure(prop: Callable[[Any], Any], value: Any) -> Optional[BaseException]:
    """Return the failure for ``value`` (exception or False), else None."""
    try:
        result = prop(value)
    except Exception as exc:  # a raised property is a failing property
        return exc
    if result is False:
        return AssertionError(f"property returned False for {value!r}")
    return None


def _shrink(tree: Tree, prop: Callable[[Any], Any]):
    """Greedy descent: repeatedly accept the first still-failing shrink."""
    steps = 0
    node = tree
    while True:
        moved = False
        for child in node.children:
            if _failure(prop, child.value) is not None:
                node = child
                steps += 1
                moved = True
                break
        if not moved:
            return node, steps


def for_all(
    gen: Gen,
    prop: Callable[[Any], Any],
    runs: int = 100,
    seed: Optional[int] = None,
    max_size: int = 30,
) -> CheckResult:
    """Check ``prop`` over ``runs`` generated inputs.

    A property passes by returning True/None and fails by returning False or
    raising.  ``seed`` fully determines the input sequence; when omitted a
    random seed is used and reported in the result.

    Sizes are ramped cyclically from 1 to ``max_size`` so small/empty inputs
    are always exercised, not only large ones.
    """
    if seed is None:
        seed = random.SystemRandom().randrange(2**63)
    rng = random.Random(seed)

    for i in range(runs):
        size = 1 + (i % max_size)
        tree = gen.draw(rng, size)
        if _failure(prop, tree.value) is not None:
            minimal_tree, steps = _shrink(tree, prop)
            error = _failure(prop, minimal_tree.value)
            return CheckResult(
                ok=False,
                seed=seed,
                runs=i + 1,
                counterexample=CounterExample(
                    original=tree.value,
                    minimal=minimal_tree.value,
                    shrink_steps=steps,
                ),
                error=error,
            )

    return CheckResult(ok=True, seed=seed, runs=runs)
