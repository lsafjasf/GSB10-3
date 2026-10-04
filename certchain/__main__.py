"""CLI: python3 -m certchain --anchor ROOT.pem [--anchor ...] \
       --intermediate DIR-or-PEM [...] --leaf LEAF.pem [--time ISO]"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from .builder import TrustStore
from .validator import verify
from .x509 import load_certificates


def _load_pool(paths):
    pool = []
    for path in paths:
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.endswith((".pem", ".crt", ".cer", ".der")):
                    pool.extend(load_certificates(os.path.join(path, name)))
        else:
            pool.extend(load_certificates(path))
    return pool


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="certchain",
        description="Build & validate a certificate chain against pinned "
                    "trust anchors (standard library only).")
    parser.add_argument("--anchor", action="append", required=True,
                        help="trust anchor PEM (repeatable); ONLY these "
                             "roots are trusted")
    parser.add_argument("--intermediate", action="append", default=[],
                        help="PEM file or directory with untrusted "
                             "intermediates (repeatable)")
    parser.add_argument("--leaf", required=True, help="end-entity PEM")
    parser.add_argument("--time", default=None,
                        help="validation time, ISO-8601 (default: now)")
    args = parser.parse_args(argv)

    anchors = []
    for path in args.anchor:
        anchors.extend(load_certificates(path))
    store = TrustStore(anchors)
    pool = _load_pool(args.intermediate)
    (leaf,) = load_certificates(args.leaf)

    when = None
    if args.time:
        when = datetime.fromisoformat(args.time)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)

    result = verify(leaf, pool, store, when=when)
    print(result.describe())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
