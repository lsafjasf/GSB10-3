"""Self-tests and demonstration data for connected_components.py."""

from collections import deque
import time
import unittest

from connected_components import count_unlabeled_foreground, label_components


def make_checkerboard(height, width):
    return [[(y + x) % 2 == 0 for x in range(width)] for y in range(height)]


def neighbor_offsets(connectivity):
    offsets = ((-1, 0), (1, 0), (0, -1), (0, 1))
    if connectivity == 8:
        offsets += ((-1, -1), (-1, 1), (1, -1), (1, 1))
    return offsets


class ConnectedComponentsTests(unittest.TestCase):
    def assert_valid_labeling(self, image, labels, component_count, connectivity):
        self.assertEqual(len(image), len(labels))
        self.assertTrue(
            all(len(image_row) == len(label_row) for image_row, label_row in zip(image, labels))
        )
        self.assertEqual(0, count_unlabeled_foreground(image, labels))

        foreground_count = sum(bool(pixel) for row in image for pixel in row)
        labeled_foreground_count = sum(label != 0 for row in labels for label in row)
        self.assertEqual(foreground_count, labeled_foreground_count)

        used_labels = {label for row in labels for label in row if label != 0}
        self.assertEqual(set(range(1, component_count + 1)), used_labels)

        for y, row in enumerate(image):
            for x, pixel in enumerate(row):
                if not pixel:
                    self.assertEqual(0, labels[y][x])

        offsets = neighbor_offsets(connectivity)
        for y, row in enumerate(labels):
            for x, label in enumerate(row):
                if label == 0:
                    continue
                for dy, dx in offsets:
                    ny = y + dy
                    nx = x + dx
                    if 0 <= ny < len(labels) and 0 <= nx < len(labels[ny]) and labels[ny][nx] != 0:
                        self.assertEqual(label, labels[ny][nx])

        for expected_label in range(1, component_count + 1):
            pixels = {
                (y, x)
                for y, row in enumerate(labels)
                for x, label in enumerate(row)
                if label == expected_label
            }
            self.assertTrue(pixels)
            start = next(iter(pixels))
            seen = {start}
            queue = deque((start,))
            while queue:
                y, x = queue.popleft()
                for dy, dx in offsets:
                    point = (y + dy, x + dx)
                    if point in pixels and point not in seen:
                        seen.add(point)
                        queue.append(point)
            self.assertEqual(pixels, seen)

    def check_both_connectivities(self, image, expected_counts):
        for connectivity, expected_count in expected_counts.items():
            with self.subTest(connectivity=connectivity):
                labels, component_count = label_components(image, connectivity)
                self.assertEqual(expected_count, component_count)
                self.assert_valid_labeling(image, labels, component_count, connectivity)

    def test_all_foreground(self):
        image = [[1] * 7 for _ in range(5)]
        self.check_both_connectivities(image, {4: 1, 8: 1})

    def test_all_background(self):
        image = [[0] * 7 for _ in range(5)]
        self.check_both_connectivities(image, {4: 0, 8: 0})

    def test_checkerboard(self):
        image = make_checkerboard(8, 8)
        self.check_both_connectivities(image, {4: 32, 8: 1})

    def test_single_foreground_pixel(self):
        self.check_both_connectivities([[1]], {4: 1, 8: 1})

    def test_single_background_pixel(self):
        self.check_both_connectivities([[0]], {4: 0, 8: 0})

    def test_diagonal_difference(self):
        image = [
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
        ]
        self.check_both_connectivities(image, {4: 3, 8: 1})

    def test_empty_image(self):
        self.check_both_connectivities([], {4: 0, 8: 0})

    def test_invalid_connectivity(self):
        with self.assertRaisesRegex(ValueError, "connectivity"):
            label_components([[1]], connectivity=6)

    def test_ragged_image(self):
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            label_components([[1, 0], [1]], connectivity=4)

    def test_large_checkerboard_is_stable_without_recursion(self):
        height = 1000
        width = 1000
        image = make_checkerboard(height, width)
        expected_four = (height * width + 1) // 2

        started = time.perf_counter()
        labels_4, count_4 = label_components(image, connectivity=4)
        four_elapsed = time.perf_counter() - started

        started = time.perf_counter()
        labels_8, count_8 = label_components(image, connectivity=8)
        eight_elapsed = time.perf_counter() - started

        self.assertEqual(expected_four, count_4)
        self.assertEqual(1, count_8)
        self.assertEqual(0, count_unlabeled_foreground(image, labels_4))
        self.assertEqual(0, count_unlabeled_foreground(image, labels_8))
        self.assertEqual(height * width, sum(len(row) for row in labels_4))
        self.assertEqual(height * width, sum(len(row) for row in labels_8))

        print(
            "\nLarge-image stability: "
            f"{height}x{width} checkerboard, "
            f"4-connectivity={count_4} regions in {four_elapsed:.3f}s, "
            f"8-connectivity={count_8} region in {eight_elapsed:.3f}s"
        )


def print_connectivity_comparison():
    cases = [
        ("3x3 diagonal", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
        ("8x8 checkerboard", make_checkerboard(8, 8)),
        ("5x7 all foreground", [[1] * 7 for _ in range(5)]),
        ("5x7 all background", [[0] * 7 for _ in range(5)]),
        ("1x1 foreground", [[1]]),
    ]

    print("Connectivity comparison")
    print("case                  foreground  4-connected  8-connected")
    for name, image in cases:
        _, count_4 = label_components(image, connectivity=4)
        _, count_8 = label_components(image, connectivity=8)
        foreground = sum(bool(pixel) for row in image for pixel in row)
        print(f"{name:<22}{foreground:>6}{count_4:>13}{count_8:>13}")


if __name__ == "__main__":
    print_connectivity_comparison()
    unittest.main(verbosity=2, exit=False)
