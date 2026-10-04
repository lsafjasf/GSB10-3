"""Group timestamped samples into windows."""

from .thresholds import BATCH_SIZE, FLUSH_INTERVAL


def aggregate(samples):
    """samples: iterable of (timestamp, value) sorted by timestamp.

    A window closes when it holds BATCH_SIZE samples, or when a sample
    arrives FLUSH_INTERVAL or more seconds after the window start.
    Returns a list of {"start", "count", "total"} dicts.
    """
    windows = []
    start = None
    count = 0
    total = 0.0
    for ts, value in samples:
        if start is None:
            start = ts
        if count >= BATCH_SIZE or ts - start >= FLUSH_INTERVAL:
            windows.append({"start": start, "count": count, "total": total})
            start = ts
            count = 0
            total = 0.0
        count += 1
        total += value
    if count:
        windows.append({"start": start, "count": count, "total": total})
    return windows
