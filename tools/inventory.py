#!/usr/bin/env python3
"""Print the threshold inventory: every definition site, value, and status.

Usage: python3 -m tools.inventory [--markdown]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import scanner  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PACKAGES = ("legacy", "legacy_tests")


def collect():
    definitions = scanner.scan_package(ROOT, BASELINE_PACKAGES)
    prod = scanner.production_names(definitions)
    rows = []
    for definition in sorted(definitions, key=lambda d: (d.name, d.path, d.lineno)):
        rel = os.path.relpath(definition.path, ROOT)
        rows.append(
            (
                definition.name,
                "%s:%d" % (rel, definition.lineno),
                repr(definition.value),
                definition.classify(prod),
            )
        )
    return rows


def main(argv):
    rows = collect()
    markdown = "--markdown" in argv
    header = ("constant", "location", "value", "status")
    if markdown:
        print("| " + " | ".join(header) + " |")
        print("| " + " | ".join("---" for _ in header) + " |")
        for row in rows:
            print("| " + " | ".join(row) + " |")
    else:
        widths = [max(len(r[i]) for r in [header] + rows) for i in range(4)]
        print("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print("  ".join(row[i].ljust(widths[i]) for i in range(4)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
