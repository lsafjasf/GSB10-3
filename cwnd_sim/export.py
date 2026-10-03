"""Export window samples as deterministic text (CSV / aligned table)."""

from __future__ import annotations

import csv
import io
from typing import Iterable, List

from .simulator import WindowSample

FIELDS = [
    "flight",
    "t_send_ms",
    "rtt_ms",
    "cwnd",
    "ssthresh",
    "flight_size",
    "lost",
    "event",
    "cwnd_after",
    "t_ack_ms",
]

_FLOAT_FIELDS = {"t_send_ms", "rtt_ms", "cwnd", "ssthresh", "cwnd_after", "t_ack_ms"}


def _format(field: str, value) -> str:
    if field in _FLOAT_FIELDS:
        return f"{float(value):.2f}"
    return str(value)


def samples_to_csv(samples: Iterable[WindowSample]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(FIELDS)
    for s in samples:
        row = s.to_row()
        writer.writerow([_format(f, row[f]) for f in FIELDS])
    return buf.getvalue()


def samples_to_table(samples: Iterable[WindowSample]) -> str:
    rows: List[List[str]] = [FIELDS]
    for s in samples:
        row = s.to_row()
        rows.append([_format(f, row[f]) for f in FIELDS])
    widths = [max(len(r[i]) for r in rows) for i in range(len(FIELDS))]
    lines = []
    for r in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip())
    return "\n".join(lines) + "\n"


def write_csv(samples: Iterable[WindowSample], path: str) -> None:
    with open(path, "w", newline="") as fh:
        fh.write(samples_to_csv(samples))
