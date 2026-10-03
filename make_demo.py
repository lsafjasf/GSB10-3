#!/usr/bin/env python3
"""Build a demo store file that mimics a crash-interrupted paged store.

The file deliberately contains: duplicate keys across pages, a same-page
duplicate, mixed page sizes, a CRC-corrupted page, magic-looking garbage and
a torn trailing page.  Run rebuild_index.py on it to see the recovery.
"""

import os

from pagestore.format import HEADER_SIZE, MAGIC, encode_page, encode_records

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "demo", "store.pst")


def main():
    pages = [
        encode_page(0, encode_records([(b"user:1", b"alice@v0"), (b"user:2", b"bob@v0")]), page_size=128),
        encode_page(1, encode_records([(b"user:1", b"alice@v1"), (b"user:3", b"carol@v1")])),
        encode_page(2, encode_records([(b"user:2", b"bob@v2"), (b"user:2", b"bob@v2-final")]), page_size=256),
        encode_page(4, encode_records([(b"user:1", b"alice@v4"), (b"user:4", b"dan@v4")]), page_size=4096),
    ]
    # page 3 was being written when the crash hit: flip payload -> CRC fails
    corrupt = bytearray(encode_page(3, encode_records([(b"user:1", b"alice@v3-lost")])))
    corrupt[HEADER_SIZE + 2] ^= 0xFF

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as fh:
        fh.write(pages[0])
        fh.write(pages[1])
        fh.write(pages[2])
        fh.write(bytes(corrupt))
        fh.write(b"\xde\xad\xbe\xef" + MAGIC + b"\x00" * 9)  # garbage incl. fake magic
        fh.write(pages[3])
        fh.write(encode_page(5, encode_records([(b"user:5", b"eve@v5")]))[:17])  # torn page
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
