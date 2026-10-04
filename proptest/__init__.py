"""proptest: a tiny property-based testing framework (Python stdlib only).

Core concepts:
  - Gen:   composable random generator, parameterized by a `size` knob.
  - Tree:  a generated value plus its lazy shrink candidates.
  - for_all: run a property many times; on failure, shrink to a minimal
    counterexample. A seed makes every run fully reproducible.
"""

from .gen import (
    Gen,
    Tree,
    booleans,
    constant,
    dicts,
    floats,
    integers,
    json_values,
    lists,
    one_of,
    recursive,
    sample,
    sized,
    text,
    tuples,
)
from .runner import CheckResult, CounterExample, for_all

__all__ = [
    "Gen",
    "Tree",
    "booleans",
    "constant",
    "dicts",
    "floats",
    "integers",
    "json_values",
    "lists",
    "one_of",
    "recursive",
    "sample",
    "sized",
    "text",
    "tuples",
    "CheckResult",
    "CounterExample",
    "for_all",
]
