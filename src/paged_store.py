"""Paged key/value store with read-only index rebuild.

On-disk layout (little endian)
------------------------------
File  := Page*
Page  := Header Record* zero-padding
Header (20 bytes):
    magic       4 bytes  = b"PGS1"
    page_no     uint32   0-based page number
    rec_count   uint32   number of records on the page
    used_len    uint32   bytes used after the header (records + padding gap is 0)
    crc32       uint32   crc32 over b"PGS1" + page_no + rec_count + used_len
                         and the record area [20 : 20 + used_len]
Record:
    key_len     uint16
    val_len     uint32
    key bytes
    value bytes

The index that is rebuilt is a pure location map:
    key -> {"page": int, "offset": int}   (offset is the record offset
    inside the page, after the header)

Lookup is done by reading the single referenced page, so a rebuilt index is
usable directly.
"""

from __future__ import annotations

import binascii
import json
import struct
from dataclasses import dataclass, field
from typing import BinaryIO

MAGIC = b"PGS1"
HEADER = struct.Struct("<4sIIII")
HEADER_SIZE = HEADER.size  # 20
DEFAULT_PAGE_SIZE = 4096


class PageError(Exception):
    """Raised when a single page is unreadable / corrupt."""


class PageFullError(Exception):
    """Raised by the writer when a record does not fit a page."""


# --------------------------------------------------------------------------
# Record / page codec
# --------------------------------------------------------------------------

def _encode_record(key: bytes, value: bytes) -> bytes:
    if len(key) > 0xFFFF:
        raise ValueError("key too long (max 65535 bytes)")
    if len(value) > 0xFFFFFFFF:
        raise ValueError("value too long")
    return struct.pack("<HI", len(key), len(value)) + key + value


def _decode_records(buf: bytes, rec_count: int) -> list[tuple[bytes, bytes, int]]:
    """Return [(key, value, record_offset_within_page), ...]."""
    out = []
    pos = 0
    for _ in range(rec_count):
        if pos + 6 > len(buf):
            raise PageError("record header truncated")
        key_len, val_len = struct.unpack_from("<HI", buf, pos)
        rec_start = pos
        pos += 6
        if pos + key_len + val_len > len(buf):
            raise PageError("record body truncated")
        key = bytes(buf[pos : pos + key_len])
        pos += key_len
        value = bytes(buf[pos : pos + val_len])
        pos += val_len
        out.append((key, value, HEADER_SIZE + rec_start))
    if pos != len(buf):
        raise PageError("used_len disagrees with record layout")
    return out


def _build_page(page_no: int, records: list[tuple[bytes, bytes]]) -> bytes:
    body = b"".join(_encode_record(k, v) for k, v in records)
    head_prefix = struct.pack("<4sIII", MAGIC, page_no, len(records), len(body))
    crc = binascii.crc32(head_prefix + body) & 0xFFFFFFFF
    return HEADER.pack(MAGIC, page_no, len(records), len(body), crc) + body


def _parse_page(raw: bytes, expected_page_no: int):
    """Validate one page and return (records, page_size). Raises PageError."""
    if len(raw) < HEADER_SIZE:
        raise PageError("page shorter than header")
    magic, page_no, rec_count, used_len, crc = HEADER.unpack(raw[:HEADER_SIZE])
    if magic != MAGIC:
        raise PageError("bad magic")
    if page_no != expected_page_no:
        raise PageError(
            f"page number mismatch: header says {page_no}, expected {expected_page_no}"
        )
    if used_len > len(raw) - HEADER_SIZE:
        raise PageError("used_len exceeds page size")
    body = raw[HEADER_SIZE : HEADER_SIZE + used_len]
    head_prefix = struct.pack("<4sIII", MAGIC, page_no, rec_count, used_len)
    if (binascii.crc32(head_prefix + body) & 0xFFFFFFFF) != crc:
        raise PageError("checksum mismatch")
    records = _decode_records(body, rec_count)
    return records


# --------------------------------------------------------------------------
# Rebuild report
# --------------------------------------------------------------------------

@dataclass
class RebuildReport:
    page_size: int
    file_size: int
    pages_scanned: int = 0
    corrupt_pages: list[dict] = field(default_factory=list)   # {page,offset,reason}
    # key -> list of {page,offset}; pages in ascending order
    occurrences: dict = field(default_factory=dict)

    @property
    def conflict_count(self) -> int:
        return sum(1 for occ in self.occurrences.values() if len(occ) > 1)

    def conflict_list(self) -> list[dict]:
        """Complete conflict list, including winner (largest page number)."""
        result = []
        for key, occ in self.occurrences.items():
            if len(occ) <= 1:
                continue
            winner = max(occ, key=lambda o: (o["page"], o["offset"]))
            result.append(
                {
                    "key": key.decode("utf-8", "replace"),
                    "winner": winner,
                    "occurrences": occ,
                }
            )
        result.sort(key=lambda c: c["key"])
        return result

    def to_dict(self) -> dict:
        return {
            "page_size": self.page_size,
            "file_size": self.file_size,
            "pages_scanned": self.pages_scanned,
            "corrupt_pages": self.corrupt_pages,
            "conflicts": self.conflict_list(),
        }


# --------------------------------------------------------------------------
# Read-only scanner / rebuild
# --------------------------------------------------------------------------

def rebuild_index(
    path: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    *,
    fp: BinaryIO | None = None,
) -> tuple[dict, RebuildReport]:
    """Scan ``path`` page by page, read-only, and rebuild the location index.

    Returns (index, report). Corrupt pages are skipped and recorded in the
    report; a corrupt page never aborts the whole scan.

    index: {key (bytes): {"page": int, "offset": int}}  -- for a key seen on
    several pages the occurrence on the largest page number wins.
    """
    if page_size <= HEADER_SIZE:
        raise ValueError("page_size too small")

    close_after = fp is None
    if fp is None:
        fp = open(path, "rb")
    try:
        fp.seek(0, 2)
        file_size = fp.tell()
        report = RebuildReport(page_size=page_size, file_size=file_size)

        page_no = 0
        while True:
            offset = page_no * page_size
            if offset >= file_size:
                break
            fp.seek(offset)
            raw = fp.read(page_size)
            report.pages_scanned += 1

            if len(raw) < page_size:
                # Inconsistent page size: truncated / shorter last page.
                report.corrupt_pages.append(
                    {
                        "page": page_no,
                        "offset": offset,
                        "reason": (
                            f"partial page: {len(raw)} of {page_size} bytes "
                            "(file size is not a multiple of the page size)"
                        ),
                    }
                )
            else:
                try:
                    records = _parse_page(raw, page_no)
                except PageError as exc:
                    report.corrupt_pages.append(
                        {"page": page_no, "offset": offset, "reason": str(exc)}
                    )
                else:
                    for key, _value, rec_offset in records:
                        report.occurrences.setdefault(key, []).append(
                            {"page": page_no, "offset": rec_offset}
                        )
            page_no += 1

        index = {}
        for key, occ in report.occurrences.items():
            # Largest page wins; on the same page the later record wins,
            # matching append-only overwrite semantics.
            index[key] = max(occ, key=lambda o: (o["page"], o["offset"]))
        return index, report
    finally:
        if close_after:
            fp.close()


# --------------------------------------------------------------------------
# Lookup against a (possibly rebuilt) index
# --------------------------------------------------------------------------

def lookup(path: str, index: dict, key: bytes | str, *,
           page_size: int = DEFAULT_PAGE_SIZE) -> bytes | None:
    """Resolve ``key`` using the location index. Returns None when absent."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    loc = index.get(key)
    if loc is None:
        return None
    with open(path, "rb") as fp:
        fp.seek(loc["page"] * page_size)
        raw = fp.read(page_size)
    try:
        records = _parse_page(raw, loc["page"])
    except PageError:
        return None
    for rec_key, value, off in records:
        if off == loc["offset"] and rec_key == key:
            return value
    return None


def dump_index_json(index: dict) -> str:
    serializable = {
        k.decode("utf-8", "surrogateescape"): v for k, v in index.items()
    }
    return json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True)


def load_index_json(text: str) -> dict:
    raw = json.loads(text)
    return {k.encode("utf-8", "surrogateescape"): v for k, v in raw.items()}


# --------------------------------------------------------------------------
# Writer (used to create stores and by tests; not needed for recovery)
# --------------------------------------------------------------------------

class PagedStoreWriter:
    """Append-only writer that builds the file and the authoritative index."""

    def __init__(self, path: str, page_size: int = DEFAULT_PAGE_SIZE):
        if page_size <= HEADER_SIZE + 6:
            raise ValueError("page_size too small")
        self.path = path
        self.page_size = page_size
        self.fp = open(path, "wb")
        self.page_no = -1
        self.records: list[tuple[bytes, bytes]] = []
        self.index: dict = {}

    def _flush(self) -> None:
        if self.page_no < 0:
            return
        page = _build_page(self.page_no, self.records)
        assert len(page) <= self.page_size
        self.fp.write(page + b"\x00" * (self.page_size - len(page)))
        self.records = []

    def put(self, key: bytes | str, value: bytes | str) -> None:
        if isinstance(key, str):
            key = key.encode("utf-8")
        if isinstance(value, str):
            value = value.encode("utf-8")
        rec = _encode_record(key, value)
        need = len(b"".join(_encode_record(k, v) for k, v in self.records)) + len(rec)
        need_new_page = need > self.page_size - HEADER_SIZE
        if need_new_page and self.records:
            self._flush()
        if len(rec) > self.page_size - HEADER_SIZE:
            raise PageFullError("record larger than one page")
        if not self.records:
            self.page_no += 1
        rec_offset = HEADER_SIZE + sum(
            len(_encode_record(k, v)) for k, v in self.records
        )
        self.records.append((key, value))
        # Append-only overwrite semantics: later write always wins, and later
        # writes always live on a page with a number >= the previous one.
        self.index[key] = {"page": self.page_no, "offset": rec_offset}

    def close(self) -> None:
        self._flush()
        self.fp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
