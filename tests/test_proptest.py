"""Self-tests for the proptest framework (stdlib unittest only)."""

import json
import string
import unittest

from proptest import (
    booleans,
    constant,
    dicts,
    floats,
    for_all,
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


def is_sorted(xs):
    return all(a <= b for a, b in zip(xs, xs[1:]))


class TestInvariantHolds(unittest.TestCase):
    """Properties that are true must pass every generated input."""

    def test_reverse_twice_is_identity(self):
        result = for_all(
            lists(integers(-50, 50)),
            lambda xs: list(reversed(list(reversed(xs)))) == xs,
            runs=200,
            seed=1,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.runs, 200)

    def test_sorted_output_is_sorted_and_same_length(self):
        result = for_all(
            lists(integers(-100, 100)),
            lambda xs: is_sorted(sorted(xs)) and len(sorted(xs)) == len(xs),
            runs=200,
            seed=2,
        )
        self.assertTrue(result.ok)

    def test_json_roundtrip_on_nested_values(self):
        scalars = one_of(
            constant(None),
            booleans(),
            integers(-1000, 1000),
            text(string.ascii_lowercase, max_length=5),
        )
        gen = recursive(
            scalars,
            lambda smaller: one_of(
                lists(smaller, max_length=3),
                dicts(text(string.ascii_lowercase, max_length=3), smaller,
                      max_length=3),
            ),
        )
        result = for_all(
            gen,
            lambda v: json.loads(json.dumps(v)) == v,
            runs=200,
            seed=3,
        )
        self.assertTrue(result.ok)


class TestInvariantBroken(unittest.TestCase):
    """Broken properties must fail and shrink to a minimal counterexample."""

    def test_all_elements_small_shrinks_to_single_element(self):
        result = for_all(
            lists(integers(0, 10)),
            lambda xs: all(x < 3 for x in xs),
            runs=200,
            seed=7,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, [3])

    def test_sorted_property_shrinks_to_inversion_pair(self):
        result = for_all(
            lists(integers(0, 9)),
            is_sorted,
            runs=200,
            seed=11,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, [1, 0])

    def test_raising_property_is_a_failure(self):
        def prop(xs):
            if sum(xs) >= 10:
                raise ValueError("sum too large")
            return True

        result = for_all(lists(integers(0, 9)), prop, runs=200, seed=13)
        self.assertFalse(result.ok)
        self.assertIsInstance(result.error, ValueError)
        minimal = result.counterexample.minimal
        self.assertGreaterEqual(sum(minimal), 10)
        # 1-minimality: removing any single element makes the property pass.
        for i in range(len(minimal)):
            self.assertLess(sum(minimal[:i] + minimal[i + 1:]), 10)

    def test_original_is_larger_than_minimal(self):
        result = for_all(
            lists(integers(0, 50)),
            lambda xs: sum(xs) <= 100,
            runs=200,
            seed=17,
        )
        self.assertFalse(result.ok)
        ce = result.counterexample
        self.assertGreater(ce.shrink_steps, 0)
        self.assertLessEqual(len(ce.minimal), len(ce.original))
        self.assertLessEqual(sum(ce.minimal), sum(ce.original))


class TestEmptyAndDegenerate(unittest.TestCase):
    """Generators must produce empty values; shrinking reaches degenerate
    inputs like [], "" and 0."""

    def test_empty_list_is_generated_and_is_minimal(self):
        result = for_all(
            lists(integers(0, 5)),
            lambda xs: len(xs) > 0,
            runs=100,
            seed=3,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, [])

    def test_empty_string_is_generated_and_is_minimal(self):
        result = for_all(text(), lambda s: s != "", runs=100, seed=4)
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, "")

    def test_shrinks_to_degenerate_zero_list(self):
        result = for_all(
            lists(integers(0, 9)),
            lambda xs: len(xs) < 3,
            runs=200,
            seed=5,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, [0, 0, 0])

    def test_integer_shrinks_to_boundary(self):
        result = for_all(integers(0, 100), lambda x: x < 5, runs=100, seed=6)
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, 5)

    def test_string_shrinks_to_degenerate(self):
        result = for_all(
            text(string.ascii_lowercase),
            lambda s: len(s) < 3,
            runs=200,
            seed=8,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, "aaa")


class TestReproducibility(unittest.TestCase):
    """Same seed must produce bit-identical results across runs."""

    def test_same_seed_same_generated_sequence(self):
        gen = lists(integers(-100, 100))
        first = sample(gen, n=100, seed=42, size=10)
        second = sample(gen, n=100, seed=42, size=10)
        self.assertEqual(first, second)

    def test_same_seed_same_check_result(self):
        prop = lambda xs: all(x < 3 for x in xs)
        first = for_all(lists(integers(0, 10)), prop, runs=200, seed=999)
        second = for_all(lists(integers(0, 10)), prop, runs=200, seed=999)
        self.assertFalse(first.ok)
        self.assertEqual(
            (first.ok, first.seed, first.runs, first.counterexample),
            (second.ok, second.seed, second.runs, second.counterexample),
        )

    def test_same_seed_same_nested_values(self):
        first = sample(json_values(), n=50, seed=123, size=8)
        second = sample(json_values(), n=50, seed=123, size=8)
        self.assertEqual(first, second)

    def test_different_seeds_differ(self):
        gen = lists(integers(-100, 100))
        self.assertNotEqual(
            sample(gen, n=20, seed=1, size=10),
            sample(gen, n=20, seed=2, size=10),
        )


class TestSizeControl(unittest.TestCase):
    """The size parameter must bound structural complexity."""

    def test_list_length_bounded_by_size(self):
        gen = lists(integers(0, 9))
        for size in (0, 1, 5, 10):
            values = sample(gen, n=50, seed=100 + size, size=size)
            self.assertTrue(all(len(v) <= size for v in values))

    def test_integer_magnitude_bounded_by_size(self):
        gen = integers(-1000, 1000)
        for size in (1, 3, 7):
            values = sample(gen, n=50, seed=200 + size, size=size)
            self.assertTrue(all(-size <= v <= size for v in values))

    def test_case_length_grows_with_size(self):
        def avg_length(size):
            values = sample(json_values(), n=40, seed=321, size=size)
            return sum(len(repr(v)) for v in values) / len(values)

        small = avg_length(1)
        large = avg_length(24)
        self.assertGreater(large, small)

    def test_sized_combinator(self):
        gen = sized(lambda n: lists(integers(0, 9), min_length=n, max_length=n))
        values = sample(gen, n=20, seed=7, size=4)
        self.assertTrue(all(len(v) == 4 for v in values))


class TestBoundaryCases(unittest.TestCase):
    """Edge cases: degenerate ranges, fixed lengths, invalid arguments."""

    def test_integer_point_range(self):
        self.assertEqual(sample(integers(5, 5), n=10, seed=0), [5] * 10)

    def test_fixed_length_list(self):
        values = sample(lists(integers(0, 9), min_length=2, max_length=2),
                        n=20, seed=0)
        self.assertTrue(all(len(v) == 2 for v in values))

    def test_size_zero_gives_empty_list(self):
        values = sample(lists(integers(0, 9)), n=20, seed=0, size=0)
        self.assertEqual(values, [[]] * 20)

    def test_invalid_ranges_rejected(self):
        with self.assertRaises(ValueError):
            integers(10, 1)
        with self.assertRaises(ValueError):
            floats(2.0, 1.0)
        with self.assertRaises(ValueError):
            text("")
        with self.assertRaises(ValueError):
            lists(integers(), min_length=-1)
        with self.assertRaises(ValueError):
            lists(integers(), min_length=3, max_length=2)
        with self.assertRaises(ValueError):
            one_of()

    def test_filter_keeps_predicate_through_shrinking(self):
        evens = integers(0, 100).filter(lambda x: x % 2 == 0)
        self.assertTrue(all(v % 2 == 0 for v in sample(evens, 50, seed=9)))
        result = for_all(evens, lambda x: x < 10, runs=100, seed=10)
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, 10)

    def test_tuples_and_dicts_shrink(self):
        gen = tuples(integers(0, 9), integers(0, 9))
        result = for_all(gen, lambda t: t[0] <= t[1], runs=200, seed=12)
        self.assertFalse(result.ok)
        self.assertEqual(result.counterexample.minimal, (1, 0))

        dgen = dicts(text(max_length=3), integers(0, 9))
        self.assertTrue(all(isinstance(d, dict) for d in sample(dgen, 20)))

    def test_assert_ok_raises_with_details(self):
        result = for_all(
            lists(integers(0, 10)),
            lambda xs: all(x < 3 for x in xs),
            runs=200,
            seed=7,
        )
        with self.assertRaises(AssertionError) as ctx:
            result.assert_ok()
        message = str(ctx.exception)
        self.assertIn("seed=7", message)
        self.assertIn("[3]", message)


if __name__ == "__main__":
    unittest.main()
