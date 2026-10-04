"""propcheck: a small property-based testing framework (stdlib only).

Features
--------
* Composable generators: const / ints / booleans / one_of / frequency /
  list_of / object_ / map / bind / sized / scale / lazy -- nested and
  recursive structures are first class.
* A ``size`` parameter flows through every generator so that test cases
  start tiny and grow with the iteration index.
* Generators produce *shrink trees*, so a failing case is automatically
  reduced to a local-minimum counterexample.
* Seeds are explicit: ``for_all(prop, gen, seed=...)`` replays byte for
  byte (same generated values, same final counterexample).

Public API: Gen, PropertyError, for_all, check.
"""

import random as _random
from copy import deepcopy as _deepcopy


class ShrinkTree:
    """A value together with a lazily computed forest of smaller variants."""

    __slots__ = ("value", "_children")

    def __init__(self, value, children=()):
        self.value = value
        self._children = children

    @property
    def children(self):
        factory = self._children
        if callable(factory):
            factory = factory()
        return [] if factory is None else factory


def _map_tree(func, tree):
    """Apply ``func`` to every value in a shrink tree, keeping its shape."""

    def expand():
        return [_map_tree(func, child) for child in tree.children]

    return ShrinkTree(func(tree.value), expand)


def _int_tree(value, lo, hi):
    """Shrink tree for an int: halving jumps toward 0, then small offsets."""

    def expand():
        candidates = []
        seen = set()

        def add(number):
            number = int(number)
            if lo <= number <= hi and number != value and number not in seen:
                seen.add(number)
                candidates.append(number)

        if value != 0:
            add(0)
        half = value // 2
        for delta in (half, half - 1 if value > 0 else half + 1):
            # Only keep steps that actually move toward zero: with half == 0
            # the second delta would otherwise point away from it.
            if delta and abs(value - delta) < abs(value):
                add(value - delta)
        if value > 0:
            for small in (1, 2):
                add(value - small)
        elif value < 0:
            for small in (-1, -2):
                add(value - small)

        return [_int_tree(number, lo, hi) for number in candidates]

    return ShrinkTree(value, expand)


def _bool_tree(value):
    if value:
        return ShrinkTree(True, lambda: [ShrinkTree(False)])
    return ShrinkTree(False)


def _list_tree(trees, min_len=0):
    """Shrink a list by removing chunks and by shrinking individual elements."""

    def expand():
        result = []
        length = len(trees)

        def add(next_trees):
            if len(next_trees) >= min_len:
                result.append(ShrinkTree(
                    [tree.value for tree in next_trees],
                    lambda ts=next_trees: _list_tree(ts, min_len).children,
                ))

        # Structural shrinking first: drop progressively larger chunks.
        if length > min_len:
            removed = length // 2
            while removed > 0:
                step = max(1, removed)
                for start in range(0, length, step):
                    end = min(length, start + removed)
                    if length - (end - start) >= min_len:
                        add(trees[:start] + trees[end:])
                removed //= 2
            if length - 1 >= min_len:
                for index in range(length):
                    add(trees[:index] + trees[index + 1:])

        # Element-wise shrinking afterwards.
        for index, tree in enumerate(trees):
            for child in tree.children:
                replaced = list(trees)
                replaced[index] = child
                add(replaced)

        return result

    return ShrinkTree([tree.value for tree in trees], expand)


def _record_tree(trees):
    """Shrink tree for a fixed set of (key, ShrinkTree) pairs."""

    def expand():
        children = []
        for index, (key, tree) in enumerate(trees):
            for child in tree.children:
                replaced = list(trees)
                replaced[index] = (key, child)
                children.append(_record_tree(tuple(replaced)))
        return children

    return ShrinkTree({key: tree.value for key, tree in trees}, expand)


class Gen:
    """A generator of shrink trees. Construct one via the static helpers."""

    __slots__ = ("_run",)

    def __init__(self, runner):
        self._run = runner

    def sample(self, rng, size):
        """Draw one shrink tree for this generator."""

        return self._run(rng, int(size))

    def generate(self, rng, size):
        """Draw one concrete value (shrink information discarded)."""

        return self.sample(rng, size).value

    def map(self, func):
        return Gen(lambda rng, size: _map_tree(func, self._run(rng, size)))

    def bind(self, func):
        """Monadic bind: choose a value, then build a generator from it.

        The RNG state used for the inner generator is captured at sampling
        time, so regenerating inner values during shrinking is deterministic.
        """

        outer = self._run

        def runner(rng, size):
            outer_tree = outer(rng, size)
            state = rng.getstate()

            def inner_for(value):
                local = _random.Random()
                local.setstate(state)
                return func(value)._run(local, size)

            return ShrinkTree(
                inner_for(outer_tree.value).value,
                lambda: [
                    _map_tree(lambda v: inner_for(v).value, child)
                    for child in outer_tree.children
                ]
                + list(inner_for(outer_tree.value).children),
            )

        return Gen(runner)

    # -- primitive generators ------------------------------------------------

    @staticmethod
    def const(value):
        return Gen(lambda rng, size: ShrinkTree(value))

    @staticmethod
    def ints(lo=-(2 ** 31), hi=2 ** 31 - 1):
        """Integers in [lo, hi]; the draw range grows with ``size``."""

        if lo > hi:
            raise ValueError("ints: lo must be <= hi")

        def runner(rng, size):
            size = max(size, 0)
            if lo >= 0:
                value = rng.randint(lo, min(hi, lo + size))
            elif hi <= 0:
                value = rng.randint(max(lo, hi - size), hi)
            else:
                value = rng.randint(max(lo, -size), min(hi, size))
            return _int_tree(value, lo, hi)

        return Gen(runner)

    @staticmethod
    def booleans():
        return Gen(lambda rng, size: _bool_tree(rng.random() < 0.5))

    @staticmethod
    def one_of(generators):
        generators = tuple(generators)
        if not generators:
            raise ValueError("one_of: at least one generator is required")
        return Gen(
            lambda rng, size: generators[rng.randrange(len(generators))]
            ._run(rng, size)
        )

    @staticmethod
    def frequency(weighted):
        """``weighted`` is a sequence of ``(weight, generator)`` pairs."""

        weighted = tuple(weighted)
        if not weighted or any(weight <= 0 for weight, _ in weighted):
            raise ValueError("frequency: need positive weights")
        total = sum(weight for weight, _ in weighted)

        def runner(rng, size):
            pick = rng.random() * total
            cumulative = 0.0
            for weight, gen in weighted:
                cumulative += weight
                if pick < cumulative:
                    return gen._run(rng, size)
            return weighted[-1][1]._run(rng, size)

        return Gen(runner)

    # -- size plumbing -------------------------------------------------------

    @staticmethod
    def sized(func):
        """Build a generator from a function ``size -> Gen``."""

        return Gen(lambda rng, size: func(size)._run(rng, size))

    @staticmethod
    def resize(size, generator):
        return Gen(lambda rng, _: generator._run(rng, size))

    @staticmethod
    def scale(factor, generator):
        return Gen(
            lambda rng, size: generator._run(rng, max(0, int(size * factor)))
        )

    @staticmethod
    def lazy(factory):
        """Defer construction (needed for recursive generators)."""

        return Gen(lambda rng, size: factory()._run(rng, size))

    # -- composite generators ------------------------------------------------

    @staticmethod
    def list_of(element, min_len=0, max_len=None):
        if min_len < 0:
            raise ValueError("list_of: min_len must be >= 0")

        def runner(rng, size):
            upper = size if max_len is None else min(max_len, size)
            length = rng.randint(min_len, max(min_len, upper))
            trees = [element._run(rng, size) for _ in range(length)]
            return _list_tree(trees, min_len)

        return Gen(runner)

    @staticmethod
    def object_(fields):
        """``fields`` maps names to generators; shrinking keeps the keys."""

        items = tuple(fields.items())
        return Gen(
            lambda rng, size: _record_tree(
                tuple((key, gen._run(rng, size)) for key, gen in items)
            )
        )


class PropertyError(AssertionError):
    """Raised by ``for_all`` when a property does not hold.

    Carries the original failing value and the shrunk counterexample.
    """

    def __init__(self, original, shrunk, tests_run, error):
        self.original = original
        self.shrunk = shrunk
        self.tests_run = tests_run
        self.error = error
        super().__init__(str(self))

    def __str__(self):
        return (
            "property failed after %d test(s)\n"
            "  original input: %r\n"
            "  shrunk to:      %r\n"
            "  raised:         %s: %s"
            % (
                self.tests_run,
                self.original,
                self.shrunk,
                type(self.error).__name__,
                self.error,
            )
        )


def _try(prop, value):
    """Return a falsifying exception for ``prop(value)``, or None.

    Both styles are supported: raising an assertion (Hypothesis style) or
    returning ``False`` (QuickCheck style).
    """

    try:
        result = prop(_deepcopy(value))
    except Exception as exc:  # noqa: BLE001 - any failure falsifies the property
        return exc
    if result is False:
        return AssertionError("property returned False")
    return None


def _shrink(prop, tree):
    """Greedily walk the shrink tree to a local-minimum failing value."""

    current = tree
    seen = {repr(tree.value)}
    while True:
        for child in current.children:
            key = repr(child.value)
            if key in seen:
                continue
            if _try(prop, child.value) is not None:
                seen.add(key)
                current = child
                break
        else:
            return current.value


def for_all(prop, gen, tests=100, seed=0, size=None):
    """Check ``prop`` against ``tests`` generated cases.

    ``size`` is either an int (fixed size) or a callable ``index -> size``;
    by default the size grows as ``min(index, 30)`` so early cases are tiny
    and later ones explore deeper input space.

    Returns the number of tests run. Raises PropertyError on failure.
    """

    if size is None:
        size_of = lambda index: min(index, 30)
    elif callable(size):
        size_of = size
    else:
        size_of = lambda index: size

    rng = _random.Random(seed)
    for index in range(tests):
        tree = gen.sample(rng, size_of(index))
        error = _try(prop, tree.value)
        if error is not None:
            shrunk = _shrink(prop, tree)
            raise PropertyError(tree.value, shrunk, index + 1, error)
    return tests


def check(prop, gen, tests=100, seed=0, size=None):
    """Non-raising variant of ``for_all``; returns a result dict."""

    try:
        ran = for_all(prop, gen, tests=tests, seed=seed, size=size)
    except PropertyError as err:
        return {
            "ok": False,
            "tests_run": err.tests_run,
            "original": err.original,
            "shrunk": err.shrunk,
            "error": err.error,
        }
    return {"ok": True, "tests_run": ran}
