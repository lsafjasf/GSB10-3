"""Connected-component labeling for binary images.

The module uses only the Python standard library. Foreground pixels are any
values whose truth value is true; background pixels are labeled zero.
"""

from collections import deque
from typing import List, Sequence, Tuple

PixelRow = Sequence[object]
BinaryImage = Sequence[PixelRow]
LabelRows = List[List[int]]

_NEIGHBORS_4 = ((-1, 0), (1, 0), (0, -1), (0, 1))
_NEIGHBORS_8 = _NEIGHBORS_4 + ((-1, -1), (-1, 1), (1, -1), (1, 1))


def label_components(image: BinaryImage, connectivity: int = 8) -> Tuple[LabelRows, int]:
    """Label connected foreground components in ``image``.

    Args:
        image: A rectangular two-dimensional sequence. Truthy values are
            foreground and falsy values are background.
        connectivity: Either ``4`` or ``8``.

    Returns:
        A ``(labels, component_count)`` pair. ``labels`` has the same shape as
        ``image``; background is ``0`` and foreground components use contiguous
        labels from ``1`` through ``component_count``.

    Raises:
        ValueError: If connectivity is unsupported or rows have different
            lengths.
    """
    if connectivity == 4:
        neighbors = _NEIGHBORS_4
    elif connectivity == 8:
        neighbors = _NEIGHBORS_8
    else:
        raise ValueError("connectivity must be 4 or 8")

    height = len(image)
    width = len(image[0]) if height else 0
    for row_index, row in enumerate(image):
        if len(row) != width:
            raise ValueError(
                f"image rows must have equal lengths: row 0 has {width}, "
                f"row {row_index} has {len(row)}"
            )

    labels: LabelRows = [[0] * width for _ in range(height)]
    component_count = 0

    for start_y in range(height):
        for start_x in range(width):
            if labels[start_y][start_x] != 0 or not image[start_y][start_x]:
                continue

            component_count += 1
            labels[start_y][start_x] = component_count
            queue = deque(((start_y, start_x),))

            while queue:
                y, x = queue.popleft()
                for dy, dx in neighbors:
                    neighbor_y = y + dy
                    neighbor_x = x + dx
                    if (
                        0 <= neighbor_y < height
                        and 0 <= neighbor_x < width
                        and labels[neighbor_y][neighbor_x] == 0
                        and image[neighbor_y][neighbor_x]
                    ):
                        labels[neighbor_y][neighbor_x] = component_count
                        queue.append((neighbor_y, neighbor_x))

    return labels, component_count


def count_unlabeled_foreground(image: BinaryImage, labels: LabelRows) -> int:
    """Return how many foreground pixels have no nonzero label."""
    if len(image) != len(labels):
        raise ValueError("image and labels must have the same height")

    unlabeled = 0
    for row_index, (image_row, label_row) in enumerate(zip(image, labels)):
        if len(image_row) != len(label_row):
            raise ValueError(f"image and labels differ in width at row {row_index}")
        unlabeled += sum(1 for pixel, label in zip(image_row, label_row) if pixel and label == 0)
    return unlabeled
