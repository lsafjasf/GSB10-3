"""Self-tests and deterministic compression report for row_filter.py."""

import random
import sys
import unittest
import zlib

from row_filter import (
    FILTER_AVERAGE,
    FILTER_NAMES,
    FILTER_NONE,
    FILTER_PAETH,
    FILTER_SUB,
    FILTER_UP,
    filter_image,
    filter_row,
    unfilter_image,
    unfilter_row,
    unfilter_stream,
)


def make_solid(width, height, bpp):
    pixel = bytes((37 + channel * 29) % 256 for channel in range(bpp))
    return tuple(pixel * width for _ in range(height))


def make_horizontal_gradient(width, height, bpp):
    rows = []
    for _ in range(height):
        row = bytearray()
        for x in range(width):
            row.extend((x * 3 + channel * 41) % 256 for channel in range(bpp))
        rows.append(bytes(row))
    return tuple(rows)


def make_vertical_gradient(width, height, bpp):
    rows = []
    for y in range(height):
        row = bytearray()
        for _ in range(width):
            row.extend((y * 3 + channel * 17) % 256 for channel in range(bpp))
        rows.append(bytes(row))
    return tuple(rows)


def make_checkerboard(width, height, bpp):
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            base = 255 if (x + y) % 2 else 0
            row.extend((base + channel * 23) % 256 for channel in range(bpp))
        rows.append(bytes(row))
    return tuple(rows)


def make_smooth_photo(width, height, bpp):
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            base = (x * 2 + y * 3 + (x * x + y * y) // 11) % 256
            row.extend((base + channel * channel * 13) % 256 for channel in range(bpp))
        rows.append(bytes(row))
    return tuple(rows)


def make_random_noise(width, height, bpp, seed=190):
    rng = random.Random(seed)
    return tuple(
        bytes(rng.randrange(256) for _ in range(width * bpp))
        for _ in range(height)
    )


def make_single_pixel_wide(height=64, bpp=4):
    return tuple(
        bytes((y * 5 + channel * 31) % 256 for channel in range(bpp))
        for y in range(height)
    )


def make_single_row_high(width=64, bpp=3):
    rng = random.Random(191)
    return (bytes(rng.randrange(256) for _ in range(width * bpp)),)


def report_images():
    return (
        ("solid", make_solid(64, 64, 4), 4),
        ("horizontal_gradient", make_horizontal_gradient(64, 64, 3), 3),
        ("vertical_gradient", make_vertical_gradient(64, 64, 3), 3),
        ("checkerboard", make_checkerboard(64, 64, 3), 3),
        ("smooth_photo", make_smooth_photo(64, 64, 3), 3),
        ("random_noise", make_random_noise(64, 64, 3), 3),
        ("single_pixel_wide", make_single_pixel_wide(), 4),
        ("single_row_high", make_single_row_high(), 3),
    )


class RowFilterTests(unittest.TestCase):
    def assert_rows_round_trip(self, rows, bpp):
        normalized_rows = tuple(bytes(row) for row in rows)
        result = filter_image(normalized_rows, bpp=bpp)
        restored = unfilter_image(
            result.filtered_rows,
            bpp=bpp,
            filter_types=result.filter_types,
        )
        self.assertEqual(len(restored), len(normalized_rows))
        for row_number, (expected, actual) in enumerate(
            zip(normalized_rows, restored), start=1
        ):
            with self.subTest(row=row_number):
                self.assertEqual(actual, expected)
        restored_from_stream = unfilter_stream(
            result.stream,
            row_length=len(normalized_rows[0]),
            bpp=bpp,
        )
        self.assertEqual(restored_from_stream, normalized_rows)
        return result

    def test_known_vectors_for_every_filter(self):
        row = bytes((10, 20, 30, 40))
        previous = bytes((5, 25, 20, 50))
        expected = {
            FILTER_NONE: bytes((10, 20, 30, 40)),
            FILTER_SUB: bytes((10, 10, 10, 10)),
            FILTER_UP: bytes((5, 251, 10, 246)),
            FILTER_AVERAGE: bytes((8, 3, 10, 0)),
            FILTER_PAETH: bytes((5, 251, 10, 246)),
        }
        for filter_type, filtered in expected.items():
            with self.subTest(filter=FILTER_NAMES[filter_type]):
                self.assertEqual(filter_row(filter_type, row, previous), filtered)
                self.assertEqual(unfilter_row(filter_type, filtered, previous), row)

    def test_adaptive_selection_uses_minimum_cost(self):
        rows = make_solid(4, 4, 1)
        result = filter_image(rows, bpp=1)
        self.assertEqual(
            result.filter_types,
            (FILTER_SUB, FILTER_UP, FILTER_UP, FILTER_UP),
        )
        self.assertEqual(result.costs, (37, 0, 0, 0))
        self.assertEqual(sum(result.distribution.values()), len(rows))

    def test_required_edge_cases_round_trip(self):
        cases = (
            ("single_pixel_wide", make_single_pixel_wide(), 4),
            ("single_row_high", make_single_row_high(), 3),
            ("random_noise", make_random_noise(17, 13, 3), 3),
            ("large_same_color", make_solid(32, 32, 4), 4),
        )
        for name, rows, bpp in cases:
            with self.subTest(case=name):
                self.assert_rows_round_trip(rows, bpp)

    def test_fixed_strategies_round_trip(self):
        rows = make_smooth_photo(19, 11, 3)
        for strategy in ("none", "sub", "up", "average", "paeth"):
            with self.subTest(strategy=strategy):
                result = filter_image(rows, bpp=3, strategy=strategy)
                restored = unfilter_image(
                    result.filtered_rows,
                    bpp=3,
                    filter_types=result.filter_types,
                )
                self.assertEqual(restored, rows)

    def test_randomized_shapes_and_channel_widths_round_trip(self):
        rng = random.Random(20261004)
        for case_number in range(30):
            bpp = rng.choice((1, 2, 3, 4, 8))
            width = rng.randint(1, 19)
            height = rng.randint(1, 19)
            rows = tuple(
                bytes(rng.randrange(256) for _ in range(width * bpp))
                for _ in range(height)
            )
            with self.subTest(case=case_number, width=width, height=height, bpp=bpp):
                self.assert_rows_round_trip(rows, bpp)

    def test_distribution_is_reported_for_every_row(self):
        rows = make_checkerboard(16, 9, 3)
        result = filter_image(rows, bpp=3)
        self.assertEqual(sum(result.distribution.values()), 9)
        self.assertTrue(set(result.distribution).issubset(FILTER_NAMES))
        self.assertEqual(len(result.costs), 9)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            filter_image([], bpp=1)
        with self.assertRaises(ValueError):
            filter_image((b"abc", b"ab"), bpp=1)
        with self.assertRaises(ValueError):
            filter_image((b"abc",), bpp=0)
        with self.assertRaises(ValueError):
            filter_image((b"abc",), bpp=2)
        with self.assertRaises(ValueError):
            filter_image((b"abc",), bpp=1, strategy="bad")
        with self.assertRaises(ValueError):
            unfilter_image((b"abc",), bpp=1, filter_types=(99,))
        with self.assertRaises(ValueError):
            unfilter_stream(b"\x00abc trailing", row_length=3)


def compressed_sizes(rows, bpp):
    strategies = ("none", "sub", "up", "average", "paeth", "adaptive")
    sizes = {}
    adaptive_result = None
    for strategy in strategies:
        result = filter_image(rows, bpp=bpp, strategy=strategy)
        sizes[strategy] = len(zlib.compress(result.stream, 9))
        if strategy == "adaptive":
            adaptive_result = result
    return sizes, adaptive_result


def print_report():
    print("\n压缩体积对比（zlib level=9，单位：字节；每行含 1 字节滤波类型）")
    print(f"{'image':<22} {'shape':<15} {'None':>7} {'Sub':>7} {'Up':>7} "
          f"{'Average':>7} {'Paeth':>7} {'Adaptive':>8}")
    distributions = []
    for name, rows, bpp in report_images():
        sizes, adaptive_result = compressed_sizes(rows, bpp)
        shape = f"{len(rows[0]) // bpp}x{len(rows)}x{bpp}"
        print(
            f"{name:<22} {shape:<15} "
            f"{sizes['none']:>7} {sizes['sub']:>7} {sizes['up']:>7} "
            f"{sizes['average']:>7} {sizes['paeth']:>7} {sizes['adaptive']:>8}"
        )
        distributions.append((name, adaptive_result.distribution))

    print("\n自适应滤波选择分布（代价：sum(abs(signed_byte))，平局按编号小者优先）")
    for name, distribution in distributions:
        details = ", ".join(
            f"{FILTER_NAMES[filter_type]}={distribution.get(filter_type, 0)}"
            for filter_type in range(FILTER_NONE, FILTER_PAETH + 1)
        )
        print(f"{name:<22} {details}")


def main():
    if "--report-only" in sys.argv:
        print_report()
        return 0
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print_report()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
