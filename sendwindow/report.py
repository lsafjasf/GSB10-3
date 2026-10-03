"""Text/CSV export of window samples, plus a hash for verification."""

import csv
import hashlib
import io

from .simulator import CSV_FIELDS, WindowSample

_FLOAT_FIELDS = ("time_start_s", "rtt_s", "duration_s",
                 "steady_throughput_Bps")


def _format(field, value):
    if value is None:
        return ""
    if field in _FLOAT_FIELDS:
        return "%.6f" % value
    return str(value)


def rows_to_csv(samples, header=True):
    """Serialize samples to CSV text with a fixed field order/precision."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if header:
        writer.writerow(CSV_FIELDS)
    for sample in samples:
        values = sample if isinstance(sample, dict) else sample.__dict__
        writer.writerow([_format(field, values[field]) for field in CSV_FIELDS])
    return buf.getvalue()


def write_csv(path, samples):
    text = rows_to_csv(samples)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
