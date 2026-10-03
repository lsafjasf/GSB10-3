#!/usr/bin/env python3
"""Read-only index rebuilder for pagestore files.

Usage:
    python3 rebuild_index.py STORE [--index-out FILE] [--report-out FILE]
                                   [--get KEY]...

Scans STORE page by page (never modifies it), rebuilds the in-memory index,
and prints a JSON report with corrupt regions and the full conflict list.
Optionally persists the rebuilt index and/or performs lookups.

Exit code is 0 even when corrupt pages were found (they are reported, not
fatal); non-zero only for usage/IO errors.
"""

import argparse
import json
import sys

from pagestore.rebuild import scan_file, RebuiltIndex


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store", help="path to the store file (opened read-only)")
    parser.add_argument("--index-out", metavar="FILE", help="write rebuilt index as JSON")
    parser.add_argument("--report-out", metavar="FILE", help="write full JSON report to FILE")
    parser.add_argument(
        "--get",
        metavar="KEY",
        action="append",
        default=[],
        help="look up KEY (utf-8) in the rebuilt index; may be repeated",
    )
    parser.add_argument(
        "--index-in",
        metavar="FILE",
        help="load a previously saved index instead of rescanning (for --get)",
    )
    args = parser.parse_args(argv)

    if args.index_in:
        index = RebuiltIndex.load(args.index_in)
        report = None
    else:
        result = scan_file(args.store)
        index = result.index
        report = result.report_dict()

    if args.index_out:
        index.save(args.index_out)

    if report is not None:
        text = json.dumps(report, indent=2, ensure_ascii=False)
        if args.report_out:
            with open(args.report_out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        print(text)

    for raw_key in args.get:
        key = raw_key.encode("utf-8")
        value = index.lookup(key)
        if value is None:
            print("GET %s -> <missing>" % raw_key)
        else:
            try:
                shown = value.decode("utf-8")
            except UnicodeDecodeError:
                shown = "0x" + value.hex()
            print("GET %s -> %s" % (raw_key, shown))

    return 0


if __name__ == "__main__":
    sys.exit(main())
