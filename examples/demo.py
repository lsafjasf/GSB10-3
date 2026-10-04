"""Demo: size-vs-length data, shrink before/after, seed reproducibility."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from proptest import (
    for_all,
    integers,
    json_values,
    lists,
    sample,
    text,
)


def show_size_vs_length():
    print("=" * 64)
    print("1. Size parameter vs. generated case length (json_values, seed=321)")
    print("=" * 64)
    print(f"{'size':>6} {'avg len(repr)':>14} {'max len':>9} {'avg depth':>10}")
    for size in (1, 2, 4, 8, 16, 32):
        values = sample(json_values(), n=40, seed=321, size=size)
        lengths = [len(repr(v)) for v in values]

        def depth(v):
            if isinstance(v, list):
                return 1 + max((depth(x) for x in v), default=0)
            if isinstance(v, dict):
                return 1 + max((depth(x) for x in v.values()), default=0)
            return 0

        depths = [depth(v) for v in values]
        print(f"{size:>6} {sum(lengths)/len(lengths):>14.1f} "
              f"{max(lengths):>9} {sum(depths)/len(depths):>10.2f}")
    print()


def show_shrink_before_after():
    print("=" * 64)
    print("2. Shrinking: before vs. after (seed=20261004)")
    print("=" * 64)

    result = for_all(
        lists(integers(0, 50)),
        lambda xs: sum(xs) <= 100,
        runs=200,
        seed=20261004,
    )
    ce = result.counterexample
    print("property : sum(xs) <= 100   (broken on purpose)")
    print(f"before   : {ce.original!r}")
    print(f"after    : {ce.minimal!r}   (shrink steps: {ce.shrink_steps})")
    print()

    result = for_all(
        lists(integers(0, 9)),
        lambda xs: all(a <= b for a, b in zip(xs, xs[1:])),
        runs=200,
        seed=11,
    )
    ce = result.counterexample
    print("property : xs is sorted     (broken on purpose)")
    print(f"before   : {ce.original!r}")
    print(f"after    : {ce.minimal!r}   (shrink steps: {ce.shrink_steps})")
    print()

    result = for_all(text(), lambda s: len(s) < 3, runs=200, seed=8)
    ce = result.counterexample
    print("property : len(s) < 3       (broken on purpose)")
    print(f"before   : {ce.original!r}")
    print(f"after    : {ce.minimal!r}   (shrink steps: {ce.shrink_steps})")
    print()


def show_reproducibility():
    print("=" * 64)
    print("3. Reproducibility: same seed twice -> identical results")
    print("=" * 64)
    prop = lambda xs: all(x < 3 for x in xs)
    first = for_all(lists(integers(0, 10)), prop, runs=200, seed=999)
    second = for_all(lists(integers(0, 10)), prop, runs=200, seed=999)
    assert (first.ok, first.seed, first.runs, first.counterexample) == (
        second.ok, second.seed, second.runs, second.counterexample
    ), "same seed must give identical results"
    assert sample(json_values(), 50, seed=123, size=8) == sample(
        json_values(), 50, seed=123, size=8
    ), "same seed must generate identical value sequences"
    print("run A    :", first.counterexample)
    print("run B    :", second.counterexample)
    print("assertion: run A == run B  ->  PASSED")
    print()


def show_empty_and_degenerate():
    print("=" * 64)
    print("4. Empty values and degenerate minimal inputs")
    print("=" * 64)
    cases = [
        ("len(xs) > 0 fails on []", lists(integers(0, 5)),
         lambda xs: len(xs) > 0, 3),
        ("s != '' fails on ''", text(), lambda s: s != "", 4),
        ("len(xs) < 3 shrinks to [0, 0, 0]", lists(integers(0, 9)),
         lambda xs: len(xs) < 3, 5),
    ]
    for label, gen, prop, seed in cases:
        result = for_all(gen, prop, runs=200, seed=seed)
        ce = result.counterexample
        print(f"{label:<34} before={ce.original!r} -> minimal={ce.minimal!r}")
    print()


if __name__ == "__main__":
    show_size_vs_length()
    show_shrink_before_after()
    show_reproducibility()
    show_empty_and_degenerate()
