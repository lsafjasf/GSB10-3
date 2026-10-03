"""PNG-style scanline filtering with adaptive per-row filter selection."""

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple, Union

FILTER_NONE = 0
FILTER_SUB = 1
FILTER_UP = 2
FILTER_AVERAGE = 3
FILTER_PAETH = 4

FILTER_NAMES = {
    FILTER_NONE: "None",
    FILTER_SUB: "Sub",
    FILTER_UP: "Up",
    FILTER_AVERAGE: "Average",
    FILTER_PAETH: "Paeth",
}

FIXED_STRATEGIES = {
    "none": FILTER_NONE,
    "sub": FILTER_SUB,
    "up": FILTER_UP,
    "average": FILTER_AVERAGE,
    "paeth": FILTER_PAETH,
}
STRATEGY_ADAPTIVE = "adaptive"

BytesLike = Union[bytes, bytearray, memoryview]


@dataclass(frozen=True)
class FilteredImage:
    """Filtered rows plus the filter selected for each row."""

    filtered_rows: Tuple[bytes, ...]
    filter_types: Tuple[int, ...]
    costs: Tuple[int, ...]
    bpp: int

    @property
    def distribution(self) -> Counter:
        return Counter(self.filter_types)

    @property
    def stream(self) -> bytes:
        """Return PNG-style bytes: one filter-type byte before each row."""
        return b"".join(
            bytes((filter_type,)) + row
            for filter_type, row in zip(self.filter_types, self.filtered_rows)
        )


def paeth_predictor(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def filter_cost(filtered_row: BytesLike) -> int:
    """Return the standard PNG heuristic: sum of absolute signed bytes."""
    return sum(abs(value - 256 if value >= 128 else value) for value in filtered_row)


def _as_bytes(value: BytesLike, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(value)


def _validate_bpp(bpp: int) -> None:
    if not isinstance(bpp, int) or bpp <= 0:
        raise ValueError("bpp must be a positive integer")


def _validate_rows(rows: Iterable[BytesLike], bpp: int) -> Tuple[Tuple[bytes, ...], int]:
    _validate_bpp(bpp)
    normalized = tuple(_as_bytes(row, "row") for row in rows)
    if not normalized:
        raise ValueError("at least one row is required")
    row_length = len(normalized[0])
    if row_length == 0:
        raise ValueError("rows must not be empty")
    if row_length % bpp != 0:
        raise ValueError("row length must be a multiple of bpp")
    if any(len(row) != row_length for row in normalized):
        raise ValueError("all rows must have the same length")
    return normalized, row_length


def _validate_filter_type(filter_type: int) -> int:
    if filter_type not in FILTER_NAMES:
        raise ValueError(f"unsupported filter type: {filter_type!r}")
    return filter_type


def filter_row(
    filter_type: int,
    row: BytesLike,
    previous_row: BytesLike = None,
    bpp: int = 1,
) -> bytes:
    """Filter one row using a PNG filter type."""
    _validate_filter_type(filter_type)
    _validate_bpp(bpp)
    source = _as_bytes(row, "row")
    if previous_row is None:
        previous = bytes(len(source))
    else:
        previous = _as_bytes(previous_row, "previous_row")
        if len(previous) != len(source):
            raise ValueError("previous_row must have the same length as row")

    if filter_type == FILTER_NONE:
        return source

    output = bytearray(len(source))
    for index, value in enumerate(source):
        left = source[index - bpp] if index >= bpp else 0
        up = previous[index]
        if filter_type == FILTER_SUB:
            predictor = left
        elif filter_type == FILTER_UP:
            predictor = up
        elif filter_type == FILTER_AVERAGE:
            predictor = (left + up) // 2
        else:
            upper_left = previous[index - bpp] if index >= bpp else 0
            predictor = paeth_predictor(left, up, upper_left)
        output[index] = (value - predictor) & 0xFF
    return bytes(output)


def unfilter_row(
    filter_type: int,
    filtered_row: BytesLike,
    previous_row: BytesLike = None,
    bpp: int = 1,
) -> bytes:
    """Reconstruct one row filtered by filter_row."""
    _validate_filter_type(filter_type)
    _validate_bpp(bpp)
    source = _as_bytes(filtered_row, "filtered_row")
    if previous_row is None:
        previous = bytes(len(source))
    else:
        previous = _as_bytes(previous_row, "previous_row")
        if len(previous) != len(source):
            raise ValueError("previous_row must have the same length as filtered_row")

    if filter_type == FILTER_NONE:
        return source

    output = bytearray(len(source))
    for index, value in enumerate(source):
        left = output[index - bpp] if index >= bpp else 0
        up = previous[index]
        if filter_type == FILTER_SUB:
            predictor = left
        elif filter_type == FILTER_UP:
            predictor = up
        elif filter_type == FILTER_AVERAGE:
            predictor = (left + up) // 2
        else:
            upper_left = previous[index - bpp] if index >= bpp else 0
            predictor = paeth_predictor(left, up, upper_left)
        output[index] = (value + predictor) & 0xFF
    return bytes(output)


def _normalize_strategy(strategy: Union[str, int]) -> Union[str, int]:
    if isinstance(strategy, str):
        normalized = strategy.lower()
        if normalized == STRATEGY_ADAPTIVE:
            return STRATEGY_ADAPTIVE
        if normalized in FIXED_STRATEGIES:
            return FIXED_STRATEGIES[normalized]
    elif isinstance(strategy, int) and strategy in FILTER_NAMES:
        return strategy
    raise ValueError(f"unsupported filter strategy: {strategy!r}")


def filter_image(
    rows: Iterable[BytesLike],
    bpp: int = 1,
    strategy: Union[str, int] = STRATEGY_ADAPTIVE,
) -> FilteredImage:
    """Filter all rows, optionally choosing the lowest-cost filter per row."""
    normalized_rows, _ = _validate_rows(rows, bpp)
    normalized_strategy = _normalize_strategy(strategy)
    previous_row = bytes(len(normalized_rows[0]))
    filtered_rows = []
    filter_types = []
    costs = []

    for row in normalized_rows:
        if normalized_strategy == STRATEGY_ADAPTIVE:
            candidates = []
            for filter_type in range(FILTER_NONE, FILTER_PAETH + 1):
                filtered = filter_row(filter_type, row, previous_row, bpp)
                candidates.append((filter_cost(filtered), filter_type, filtered))
            cost, filter_type, filtered = min(candidates)
        else:
            filter_type = normalized_strategy
            filtered = filter_row(filter_type, row, previous_row, bpp)
            cost = filter_cost(filtered)
        filtered_rows.append(filtered)
        filter_types.append(filter_type)
        costs.append(cost)
        previous_row = row

    return FilteredImage(
        filtered_rows=tuple(filtered_rows),
        filter_types=tuple(filter_types),
        costs=tuple(costs),
        bpp=bpp,
    )


def unfilter_image(
    filtered_rows: Iterable[BytesLike],
    bpp: int = 1,
    filter_types: Sequence[int] = None,
) -> Tuple[bytes, ...]:
    """Reconstruct rows from filtered rows and their per-row filter types."""
    normalized_rows, _ = _validate_rows(filtered_rows, bpp)
    if filter_types is None:
        normalized_types = (FILTER_NONE,) * len(normalized_rows)
    else:
        normalized_types = tuple(filter_types)
        if len(normalized_types) != len(normalized_rows):
            raise ValueError("filter_types length must match row count")
        for filter_type in normalized_types:
            _validate_filter_type(filter_type)

    previous_row = bytes(len(normalized_rows[0]))
    restored_rows = []
    for filter_type, filtered_row in zip(normalized_types, normalized_rows):
        restored = unfilter_row(filter_type, filtered_row, previous_row, bpp)
        restored_rows.append(restored)
        previous_row = restored
    return tuple(restored_rows)


def unfilter_stream(stream: BytesLike, row_length: int, bpp: int = 1) -> Tuple[bytes, ...]:
    """Parse and reconstruct PNG-style filtered bytes."""
    _validate_bpp(bpp)
    if not isinstance(row_length, int) or row_length <= 0:
        raise ValueError("row_length must be a positive integer")
    data = _as_bytes(stream, "stream")
    stride = row_length + 1
    if not data or len(data) % stride != 0:
        raise ValueError("stream length must be a non-empty multiple of row_length + 1")
    filter_types = []
    filtered_rows = []
    for offset in range(0, len(data), stride):
        filter_types.append(data[offset])
        filtered_rows.append(data[offset + 1 : offset + stride])
    return unfilter_image(filtered_rows, bpp=bpp, filter_types=filter_types)
