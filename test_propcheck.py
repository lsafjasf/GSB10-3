"""Self-tests for the propcheck framework (stdlib unittest only)."""

import random
import unittest

from propcheck import Gen, PropertyError, check, for_all


def tree_gen(max_len=6):
    """Recursive nested structure: ("leaf", n) | ("node", [tree, ...])."""

    def build(size):
        if size <= 0:
            return Gen.ints(0, 9).map(lambda n: ("leaf", n))
        # Halving the size for subtrees keeps the total node count bounded
        # (max_len ** log2(size)) while still producing deep nesting.
        return Gen.frequency([
            (1, Gen.ints(0, 9).map(lambda n: ("leaf", n))),
            (3, Gen.list_of(Gen.lazy(lambda: build(size // 2)), max_len=max_len)
             .map(lambda children: ("node", children))),
        ])

    return Gen.sized(build)


def leaf_count(tree):
    tag, payload = tree
    if tag == "leaf":
        return 1
    return sum(leaf_count(child) for child in payload)


class TestInvariantHolds(unittest.TestCase):
    """Case: the invariant holds -> the property passes."""

    def test_sorted_idempotent(self):
        ran = for_all(
            lambda xs: sorted(sorted(xs)) == sorted(xs),
            Gen.list_of(Gen.ints(0, 100)),
            tests=200,
            seed=1,
        )
        self.assertEqual(ran, 200)

    def test_reverse_involution(self):
        for_all(
            lambda xs: list(reversed(list(reversed(xs)))) == xs,
            Gen.list_of(Gen.ints()),
            tests=200,
            seed=2,
        )

    def test_check_reports_ok(self):
        result = check(lambda xs: sum(xs) >= sum(xs), Gen.list_of(Gen.ints()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["tests_run"], 100)


class TestInvariantBroken(unittest.TestCase):
    """Case: the invariant is broken -> minimal counterexample is found."""

    def test_sorted_shrinks_to_one_zero(self):
        with self.assertRaises(PropertyError) as ctx:
            for_all(
                lambda xs: xs == sorted(xs),
                Gen.list_of(Gen.ints(0, 5)),
                tests=200,
                seed=7,
            )
        self.assertEqual(ctx.exception.shrunk, [1, 0])
        self.assertNotEqual(ctx.exception.original, ctx.exception.shrunk)

    def test_list_element_shrinks_to_threshold(self):
        with self.assertRaises(PropertyError) as ctx:
            for_all(
                lambda xs: all(x < 10 for x in xs),
                Gen.list_of(Gen.ints(0, 100)),
                tests=200,
                seed=3,
            )
        self.assertEqual(ctx.exception.shrunk, [10])

    def test_negative_int_shrinks_to_minus_one(self):
        with self.assertRaises(PropertyError) as ctx:
            for_all(lambda x: x >= 0, Gen.ints(-100, 100), tests=200, seed=5)
        self.assertEqual(ctx.exception.shrunk, -1)

    def test_nested_tree_shrinks_to_five_leaves(self):
        with self.assertRaises(PropertyError) as ctx:
            for_all(
                lambda t: leaf_count(t) < 5,
                tree_gen(),
                tests=300,
                seed=11,
            )
        # 5 leaves is the true minimum for "leaf_count < 5" to fail.
        self.assertEqual(leaf_count(ctx.exception.shrunk), 5)
        self.assertGreaterEqual(leaf_count(ctx.exception.original), 5)
        # The shrunk value still fails the property.
        self.assertFalse(ctx.exception.shrunk and
                         leaf_count(ctx.exception.shrunk) < 5)

    def test_object_field_shrinks(self):
        gen = Gen.object_({"name": Gen.const("x"), "age": Gen.ints(0, 120)})
        with self.assertRaises(PropertyError) as ctx:
            for_all(lambda p: p["age"] < 18, gen, tests=300, seed=9)
        self.assertEqual(ctx.exception.shrunk, {"name": "x", "age": 18})

    def test_error_object_preserved(self):
        def prop(xs):
            raise ValueError("boom")

        with self.assertRaises(PropertyError) as ctx:
            for_all(prop, Gen.ints(0, 3), tests=10, seed=0)
        self.assertIsInstance(ctx.exception.error, ValueError)


class TestEmptyAndDegenerate(unittest.TestCase):
    """Cases: generators produce empty values; shrinking reaches degenerate inputs."""

    def test_empty_values_are_generated(self):
        gen = Gen.one_of([
            Gen.const(None),
            Gen.const(""),
            Gen.list_of(Gen.ints(0, 9)),
        ])
        rng = random.Random(42)
        seen = set()
        for size in range(30):
            value = gen.generate(rng, size)
            if value is None:
                seen.add("none")
            elif value == "":
                seen.add("empty_str")
            elif value == []:
                seen.add("empty_list")
        self.assertEqual(seen, {"none", "empty_str", "empty_list"})

    def test_empty_list_is_tried_and_can_be_minimal(self):
        with self.assertRaises(PropertyError) as ctx:
            for_all(lambda xs: len(xs) > 0, Gen.list_of(Gen.ints(0, 9)),
                    tests=50, seed=4)
        self.assertEqual(ctx.exception.shrunk, [])

    def test_shrinks_to_degenerate_zero(self):
        with self.assertRaises(PropertyError) as ctx:
            for_all(lambda x: x != 0, Gen.ints(0, 1000), tests=200, seed=8)
        self.assertEqual(ctx.exception.shrunk, 0)

    def test_shrinks_to_degenerate_false(self):
        with self.assertRaises(PropertyError) as ctx:
            for_all(lambda b: b, Gen.booleans(), tests=50, seed=0)
        self.assertEqual(ctx.exception.shrunk, False)

    def test_min_len_bounds_shrinking(self):
        gen = Gen.list_of(Gen.ints(0, 9), min_len=1)
        with self.assertRaises(PropertyError) as ctx:
            for_all(lambda xs: len(xs) > 1, gen, tests=100, seed=6)
        self.assertEqual(ctx.exception.shrunk, [0])
        self.assertEqual(len(ctx.exception.shrunk), 1)


class TestReproducibility(unittest.TestCase):
    """Case: the same seed replays byte for byte."""

    def test_same_seed_same_values(self):
        gen = Gen.list_of(Gen.ints(-50, 50))
        run_a = [gen.generate(random.Random(99), i) for i in range(50)]
        run_b = [gen.generate(random.Random(99), i) for i in range(50)]
        self.assertEqual(run_a, run_b)

    def test_same_seed_same_counterexample(self):
        def run():
            return check(
                lambda xs: all(x < 10 for x in xs),
                Gen.list_of(Gen.ints(0, 100)),
                tests=200,
                seed=1234,
            )

        first, second = run(), run()
        self.assertFalse(first["ok"])
        self.assertEqual(first["tests_run"], second["tests_run"])
        self.assertEqual(first["original"], second["original"])
        self.assertEqual(first["shrunk"], second["shrunk"])

    def test_same_seed_nested_structures(self):
        gen = tree_gen()
        run_a = [gen.generate(random.Random(7), i) for i in range(40)]
        run_b = [gen.generate(random.Random(7), i) for i in range(40)]
        self.assertEqual(run_a, run_b)

    def test_different_seeds_diverge(self):
        gen = Gen.list_of(Gen.ints(0, 10 ** 6))
        run_a = [gen.generate(random.Random(1), 10) for _ in range(20)]
        run_b = [gen.generate(random.Random(2), 10) for _ in range(20)]
        self.assertNotEqual(run_a, run_b)


class TestSizeControl(unittest.TestCase):
    """Case: the size parameter controls case complexity."""

    def test_list_length_bounded_by_size(self):
        gen = Gen.list_of(Gen.ints(0, 9))
        rng = random.Random(0)
        for size in (0, 1, 5, 20):
            for _ in range(20):
                self.assertLessEqual(len(gen.generate(rng, size)), size)

    def test_average_length_grows_with_size(self):
        gen = Gen.list_of(Gen.ints(0, 9))

        def avg(size):
            rng = random.Random(0)
            return sum(len(gen.generate(rng, size)) for _ in range(200)) / 200

        small, large = avg(2), avg(20)
        self.assertGreater(large, small * 3)

    def test_nested_depth_bounded_by_size(self):
        def depth(tree):
            tag, payload = tree
            if tag == "leaf":
                return 1
            return 1 + max((depth(c) for c in payload), default=0)

        gen = tree_gen()
        rng = random.Random(0)
        for size in (0, 1, 3, 8):
            for _ in range(20):
                self.assertLessEqual(depth(gen.generate(rng, size)), size + 1)

    def test_scale_and_resize(self):
        gen = Gen.scale(3, Gen.list_of(Gen.ints(0, 9)))
        rng = random.Random(0)
        self.assertLessEqual(len(gen.generate(rng, 4)), 12)
        fixed = Gen.resize(7, Gen.list_of(Gen.ints(0, 9)))
        self.assertLessEqual(len(fixed.generate(random.Random(0), 0)), 7)


class TestCombinators(unittest.TestCase):
    def test_bind_uses_outer_value(self):
        gen = Gen.ints(0, 5).bind(
            lambda n: Gen.list_of(Gen.ints(0, 9), min_len=n, max_len=n)
        )
        rng = random.Random(0)
        for size in (0, 3, 5):
            for _ in range(30):
                value = gen.generate(rng, size)
                self.assertLessEqual(len(value), 5)

    def test_frequency_and_one_of_cover_all_branches(self):
        gen = Gen.frequency([
            (1, Gen.const("a")),
            (2, Gen.const("b")),
            (1, Gen.const("c")),
        ])
        rng = random.Random(0)
        seen = {gen.generate(rng, 1) for _ in range(200)}
        self.assertEqual(seen, {"a", "b", "c"})

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            Gen.ints(5, 1)
        with self.assertRaises(ValueError):
            Gen.one_of([])
        with self.assertRaises(ValueError):
            Gen.frequency([(0, Gen.const(1))])
        with self.assertRaises(ValueError):
            Gen.list_of(Gen.ints(), min_len=-1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
