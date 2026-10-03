#!/usr/bin/env python3
"""Read-only scan + index rebuild CLI.

Usage:
    python3 rebuild_index.py DATAFILE [--page-size N]
                                      [--index-out INDEX.json]
                                      [--report-out REPORT.json]
                                      [--lookup KEY ...]

The data file is opened read-only; corrupt pages are skipped and recorded in
the report instead of aborting the scan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paged_store import (  # noqa: E402
    DEFAULT_PAGE_SIZE,
    dump_index_json,
    load_index_json,
    lookup,
    rebuild_index,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild a paged-store index by read-only scan")
    ap.add_argument("datafile", help="paged storage data file")
    ap.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    ap.add_argument("--index-out", help="write rebuilt index as JSON")
    ap.add_argument("--report-out", help="write scan report as JSON")
    ap.add_argument("--lookup", nargs="*", metavar="KEY", default=[],
                    help="keys to look up with the rebuilt index")
    args = ap.parse_args(argv)

    index, report = rebuild_index(args.datafile, page_size=args.page_size)

    print(f"file size       : {report.file_size} bytes")
    print(f"pages scanned   : {report.pages_scanned}")
    print(f"keys recovered  : {len(index)}")
    print(f"corrupt pages   : {len(report.corrupt_pages)}")
    for c in report.corrupt_pages:
        print(f"  - page {c['page']:>6} at offset {c['offset']:>10}: {c['reason']}")
    print(f"conflicting keys: {report.conflict_count}")
    for c in report.conflict_list():
        pages = ", ".join(str(o["page"]) for o in c["occurrences"])
        print(f"  - key {c['key']!r}: seen on pages [{pages}], winner page "
              f"{c['winner']['page']}")

    for key in args.lookup:
        value = lookup(args.datafile, index, key, page_size=args.page_size)
        shown = "<missing>" if value is None else value.decode("utf-8", "replace")
        print(f"lookup {key!r} -> {shown!r}")

    if args.index_out:
        with open(args.index_out, "w", encoding="utf-8") as fp:
            fp.write(dump_index_json(index))
        print(f"index written   : {args.index_out}")
    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as fp:
            json.dump(report.to_dict(), fp, ensure_ascii=False, indent=2)
        print(f"report written  : {args.report_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
