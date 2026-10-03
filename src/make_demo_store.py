#!/usr/bin/env python3
"""Build a demo paged store: duplicate keys across pages + one corrupt page,
so the rebuild CLI has something interesting to recover."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paged_store import PagedStoreWriter  # noqa: E402


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_store.dat"
    page_size = 512
    with PagedStoreWriter(out, page_size=page_size) as w:
        for i in range(40):
            w.put(f"key-{i:02d}", f"value-v1-{i}")
        # overwrite some keys; new versions land on later (intact) pages
        for i in range(0, 40, 3):
            w.put(f"key-{i:02d}", f"value-v2-{i}")

    # corrupt page 1 (flip bytes inside a record, breaking its checksum)
    with open(out, "r+b") as fp:
        fp.seek(1 * page_size + 30)
        fp.write(b"\xde\xad\xbe\xef" * 4)

    print(f"demo store written to {out} (page 1 corrupted)")


if __name__ == "__main__":
    main()
