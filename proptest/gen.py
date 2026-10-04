"""Composable generators with integrated shrinking.

A generator is a function ``(rng, size) -> Tree[value]``.  The tree carries
the generated value together with lazily produced shrink candidates, so any
composed structure (lists, tuples, dicts, arbitrary recursion) shrinks
automatically without generator-specific knowledge in the runner.

``size`` is a non-negative integer: larger values mean larger structures.
Concretely:
  * integers with 0 in range are drawn from ``[-size, size]`` intersect the
    declared bounds;
  * lists/strings/dicts have length in ``[min_length, max(size, min_length)]``;
  * ``recursive`` subtracts one from size per nesting level, so generated
    depth is bounded by ``size``.
"""

from __future__ import annotations

import random
import string
from typing import Callable, Generic, Tuple, TypeVar

T = TypeVar("T")


class Tree(Generic[T]):
    """A generated value with its (lazily computed) shrink candidates."""

    __slots__ = ("value", "_children", "_children_fn")

    def __init__(self, value: T, children=()):
        self.value = value
        if callable(children):
            self._children_fn = children
            self._children = None
        else:
            self._children = tuple(children)
            self._children_fn = None

    @property
    def children(self) -> Tuple["Tree[T]", ...]:
        if self._children is None:
            self._children = tuple(self._children_fn())
            self._children_fn = None
        return self._children

    def map(self, fn: Callable[[T], T]) -> "Tree[T]":
        return Tree(fn(self.value), lambda: (c.map(fn) for c in self.children))

    def __repr__(self) -> str:
        return f"Tree({self.value!r})"


class Gen(Generic[T]):
    """A ``(rng, size)`` random generator of shrinkable trees."""

    def __init__(self, draw: Callable[[random.Random, int], Tree[T]]):
        self._draw = draw

    def draw(self, rng: random.Random, size: int) -> Tree[T]:
        return self._draw(rng, max(0, size))

    def example(self, seed: int = 0, size: int = 10) -> T:
        return self.draw(random.Random(seed), size).value

    def map(self, fn: Callable[[T], T]) -> "Gen[T]":
        return Gen(lambda rng, size: self.draw(rng, size).map(fn))

    def bind(self, fn: Callable[[T], "Gen[T]"]) -> "Gen[T]":
        def draw(rng, size):
            outer = self.draw(rng, size)
            inner = fn(outer.value).draw(rng, size)
            return Tree(inner.value, inner.children)

        return Gen(draw)

    def filter(self, pred: Callable[[T], bool], max_tries: int = 100) -> "Gen[T]":
        def draw(rng, size):
            for _ in range(max_tries):
                tree = self.draw(rng, size)
                if pred(tree.value):
                    return _filter_tree(tree, pred)
            raise ValueError("filter predicate could not be satisfied")

        return Gen(draw)


def _filter_tree(tree: Tree[T], pred: Callable[[T], bool]) -> Tree[T]:
    def children():
        for child in tree.children:
            if pred(child.value):
                yield _filter_tree(child, pred)

    return Tree(tree.value, children)


# ---------------------------------------------------------------------------
# Primitive generators
# ---------------------------------------------------------------------------


def _int_shrink_tree(value: int, target: int) -> Tree[int]:
    def children():
        if value == target:
            return
        yield _int_shrink_tree(target, target)
        step = (value - target) // 2
        while step:
            yield _int_shrink_tree(value - step, target)
            step //= 2

    return Tree(value, children)


def integers(lo: int = -100, hi: int = 100) -> Gen[int]:
    """Uniform integers in [lo, hi], shrinking toward 0 (or the nearer bound)."""
    if lo > hi:
        raise ValueError("lo must be <= hi")
    if lo <= 0 <= hi:
        target = 0
    elif lo > 0:
        target = lo
    else:
        target = hi

    def draw(rng, size):
        if lo <= 0 <= hi:
            eff_lo = max(lo, -size)
            eff_hi = min(hi, size)
        else:
            eff_lo, eff_hi = lo, hi
        return _int_shrink_tree(rng.randint(eff_lo, eff_hi), target)

    return Gen(draw)


def booleans() -> Gen[bool]:
    def draw(rng, size):
        value = rng.random() < 0.5
        if value:
            return Tree(True, (Tree(False),))
        return Tree(False)

    return Gen(draw)


def _float_shrink_tree(value: float, target: float) -> Tree[float]:
    def children():
        if value == target:
            return
        yield _float_shrink_tree(target, target)
        current = value
        for _ in range(32):
            nxt = target + (current - target) / 2.0
            if nxt == current or nxt == target:
                break
            yield _float_shrink_tree(nxt, target)
            current = nxt

    return Tree(value, children)


def floats(lo: float = -1_000_000.0, hi: float = 1_000_000.0) -> Gen[float]:
    """Uniform finite floats in [lo, hi], shrinking by halving toward 0."""
    if lo > hi:
        raise ValueError("lo must be <= hi")
    if lo <= 0.0 <= hi:
        target = 0.0
    else:
        target = lo if lo > 0.0 else hi

    return Gen(
        lambda rng, size: _float_shrink_tree(rng.uniform(lo, hi), target)
    )


def constant(value: T) -> Gen[T]:
    return Gen(lambda rng, size: Tree(value))


def one_of(*gens: Gen[T]) -> Gen[T]:
    """Pick one of the generators with equal probability."""
    if not gens:
        raise ValueError("one_of needs at least one generator")
    return Gen(lambda rng, size: gens[rng.randrange(len(gens))].draw(rng, size))


def sized(fn: Callable[[int], Gen[T]]) -> Gen[T]:
    """Build a generator from the current size, e.g. sized(lambda n: ...)."""
    return Gen(lambda rng, size: fn(size).draw(rng, size))


# ---------------------------------------------------------------------------
# Containers and recursion
# ---------------------------------------------------------------------------


def _list_shrink_tree(
    item_trees: Tuple[Tree[T], ...], min_length: int
) -> Tree[list]:
    values = [t.value for t in item_trees]

    def children():
        n = len(item_trees)
        # First shrink the length: remove chunks of size n/2, n/4, ...
        chunk = n // 2
        while chunk:
            if n - chunk >= min_length:
                for start in range(0, n - chunk + 1, chunk):
                    kept = item_trees[:start] + item_trees[start + chunk :]
                    if len(kept) >= min_length:
                        yield _list_shrink_tree(kept, min_length)
            chunk //= 2
        # Then shrink individual elements.
        for i, item in enumerate(item_trees):
            for smaller in item.children:
                replaced = item_trees[:i] + (smaller,) + item_trees[i + 1 :]
                yield _list_shrink_tree(replaced, min_length)

    return Tree(values, children)


def lists(
    item_gen: Gen[T], min_length: int = 0, max_length: int = None
) -> Gen[list]:
    """Lists with length drawn up to ``size``; shrink by shortening/elements."""
    if min_length < 0:
        raise ValueError("min_length must be >= 0")
    if max_length is not None and max_length < min_length:
        raise ValueError("max_length must be >= min_length")

    def draw(rng, size):
        hi = size if max_length is None else min(max_length, size)
        hi = max(hi, min_length)
        count = rng.randint(min_length, hi)
        item_trees = tuple(
            item_gen.draw(rng, max(size - 1, 0)) for _ in range(count)
        )
        return _list_shrink_tree(item_trees, min_length)

    return Gen(draw)


def _tuple_shrink_tree(item_trees: Tuple[Tree, ...]) -> Tree[tuple]:
    values = tuple(t.value for t in item_trees)

    def children():
        for i, item in enumerate(item_trees):
            for smaller in item.children:
                replaced = item_trees[:i] + (smaller,) + item_trees[i + 1 :]
                yield _tuple_shrink_tree(replaced)

    return Tree(values, children)


def tuples(*gens: Gen) -> Gen[tuple]:
    def draw(rng, size):
        return _tuple_shrink_tree(tuple(g.draw(rng, size) for g in gens))

    return Gen(draw)


def _chars(alphabet: str) -> Gen[str]:
    return Gen(
        lambda rng, size: _int_shrink_tree(
            rng.randrange(len(alphabet)), 0
        ).map(lambda i: alphabet[i])
    )


def text(
    alphabet: str = string.ascii_lowercase,
    min_length: int = 0,
    max_length: int = None,
) -> Gen[str]:
    """Strings that shrink to shorter strings and earlier alphabet chars."""
    if not alphabet:
        raise ValueError("alphabet must be non-empty")
    return lists(_chars(alphabet), min_length, max_length).map("".join)


def dicts(
    key_gen: Gen,
    value_gen: Gen,
    min_length: int = 0,
    max_length: int = None,
) -> Gen[dict]:
    pair_gen = tuples(key_gen, value_gen)
    return lists(pair_gen, min_length, max_length).map(dict)


def recursive(base: Gen[T], extend: Callable[[Gen[T]], Gen[T]]) -> Gen[T]:
    """Recursive generator: one ``base`` layer per ``size`` budget.

    ``extend`` receives a generator for smaller values and wraps them, e.g.::

        recursive(scalars, lambda smaller: lists(smaller))
    """

    def draw(rng, size):
        if size <= 0:
            return base.draw(rng, 0)
        smaller = Gen(lambda r, s: draw(r, min(s, size - 1)))
        return one_of(base, extend(smaller)).draw(rng, size)

    return Gen(draw)


def json_values() -> Gen:
    """Nested JSON-like values: None/bool/int/float/str/list/dict."""
    scalars = one_of(
        constant(None),
        booleans(),
        integers(-1000, 1000),
        floats(-1000.0, 1000.0),
        text(string.ascii_lowercase, max_length=6),
    )

    def extend(smaller):
        return one_of(
            lists(smaller, max_length=4),
            dicts(text(string.ascii_lowercase, max_length=4), smaller,
                  max_length=4),
        )

    return recursive(scalars, extend)


def sample(gen: Gen[T], n: int = 10, seed: int = 0, size: int = 10):
    """Deterministically draw ``n`` example values from ``gen``."""
    rng = random.Random(seed)
    return [gen.draw(rng, size).value for _ in range(n)]
