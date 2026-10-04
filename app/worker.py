"""Drain items to a sink in fixed-size batches."""

from .thresholds import BATCH_SIZE


def drain(items, sink):
    """Flush items to sink in batches of BATCH_SIZE. Returns flush count."""
    flushes = 0
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= BATCH_SIZE:
            sink(list(batch))
            flushes += 1
            batch = []
    if batch:
        sink(list(batch))
        flushes += 1
    return flushes
