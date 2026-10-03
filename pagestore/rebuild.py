"""Read-only page scan and index rebuild.

The scanner never writes to the store file.  It walks the file page by page;
whenever a page header or payload fails validation it records the corrupt
byte range and searches forward for the next valid page (pages are
self-describing, so mixed page sizes need no stride assumptions).  A corrupt
page therefore never aborts the scan.

Duplicate keys are resolved by "largest page number wins" (ties on the same
page: later record wins), and every conflicting occurrence is reported.
"""

import base64
import json
import struct
from dataclasses import dataclass, field

from .format import MAGIC, HEADER_SIZE, decode_records, header_candidate, PageError

INDEX_FORMAT_VERSION = 1


@dataclass
class Occurrence:
    key: bytes
    value: bytes
    page_no: int
    page_offset: int
    record_index: int
    record_offset: int


@dataclass
class Conflict:
    key: bytes
    winner: Occurrence
    losers: list  # list[Occurrence], superseded occurrences


@dataclass
class CorruptRegion:
    offset: int
    length: int
    reason: str


@dataclass
class ValidPage:
    page_no: int
    offset: int
    page_size: int
    payload_len: int
    record_count: int


@dataclass
class IndexEntry:
    key: bytes
    value: bytes
    page_no: int
    page_offset: int
    record_offset: int


@dataclass
class RebuildResult:
    path: str
    file_size: int
    valid_pages: list = field(default_factory=list)
    corrupt_regions: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    index: "RebuiltIndex" = None

    def report_dict(self):
        return {
            "file": self.path,
            "file_size": self.file_size,
            "summary": {
                "pages_valid": len(self.valid_pages),
                "pages_corrupt_regions": len(self.corrupt_regions),
                "corrupt_bytes": sum(r.length for r in self.corrupt_regions),
                "records_indexed": len(self.index),
                "conflicting_keys": len(self.conflicts),
                "duplicate_occurrences": sum(len(c.losers) for c in self.conflicts),
            },
            "valid_pages": [
                {
                    "page_no": p.page_no,
                    "offset": p.offset,
                    "page_size": p.page_size,
                    "payload_len": p.payload_len,
                    "record_count": p.record_count,
                }
                for p in self.valid_pages
            ],
            "corrupt_regions": [
                {"offset": r.offset, "length": r.length, "reason": r.reason}
                for r in self.corrupt_regions
            ],
            "conflicts": [
                {
                    "key": _b64(c.key),
                    "key_text": _text(c.key),
                    "winner": _occ_dict(c.winner),
                    "superseded": [_occ_dict(o) for o in c.losers],
                }
                for c in self.conflicts
            ],
        }


class RebuiltIndex:
    """In-memory index produced by the rebuild; usable for lookups directly."""

    def __init__(self, entries=None):
        self._entries = dict(entries or {})

    def __len__(self):
        return len(self._entries)

    def __contains__(self, key):
        return key in self._entries

    def keys(self):
        return self._entries.keys()

    def entry(self, key):
        return self._entries.get(key)

    def lookup(self, key):
        """Return the indexed value for ``key`` or ``None``."""
        entry = self._entries.get(key)
        return entry.value if entry is not None else None

    def get(self, path, key):
        """Verify the index against the store file: read the record back from
        ``path`` at the indexed offset and return its value (or ``None``)."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        with open(path, "rb") as fh:
            fh.seek(entry.record_offset)
            (key_len,) = struct.unpack(">H", fh.read(2))
            stored_key = fh.read(key_len)
            (value_len,) = struct.unpack(">I", fh.read(4))
            value = fh.read(value_len)
        if stored_key != key:
            raise PageError("index points at wrong record for key %r" % (key,))
        return value

    def items(self):
        for key in sorted(self._entries):
            yield key, self._entries[key].value

    def save(self, path):
        doc = {
            "format": "pagestore-index",
            "version": INDEX_FORMAT_VERSION,
            "entries": [
                {
                    "key": _b64(e.key),
                    "value": _b64(e.value),
                    "page_no": e.page_no,
                    "page_offset": e.page_offset,
                    "record_offset": e.record_offset,
                }
                for _, e in sorted(self._entries.items())
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("format") != "pagestore-index":
            raise ValueError("not a pagestore index file: %s" % path)
        entries = {}
        for item in doc["entries"]:
            key = base64.b64decode(item["key"])
            entries[key] = IndexEntry(
                key=key,
                value=base64.b64decode(item["value"]),
                page_no=item["page_no"],
                page_offset=item["page_offset"],
                record_offset=item["record_offset"],
            )
        return cls(entries)


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _text(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _occ_dict(occ):
    return {
        "page_no": occ.page_no,
        "page_offset": occ.page_offset,
        "record_index": occ.record_index,
        "record_offset": occ.record_offset,
        "value": _b64(occ.value),
        "value_text": _text(occ.value),
    }


def _find_next_page(data, start):
    """Return the offset of the next valid page at or after ``start``."""
    pos = start
    while True:
        pos = data.find(MAGIC, pos)
        if pos < 0:
            return -1
        if header_candidate(data, pos) is not None:
            return pos
        pos += 1


def scan_file(path):
    """Read-only scan of ``path``; returns a :class:`RebuildResult`.

    Never raises on corrupt content: every unreadable range is appended to
    ``corrupt_regions`` with its offset and the scan continues.
    """
    with open(path, "rb") as fh:
        data = fh.read()

    result = RebuildResult(path=path, file_size=len(data))
    occurrences = {}  # key -> list[Occurrence] in scan order

    pos = 0
    size = len(data)
    while pos < size:
        candidate = header_candidate(data, pos)
        if candidate is None:
            nxt = _find_next_page(data, pos + 1)
            end = nxt if nxt >= 0 else size
            result.corrupt_regions.append(
                CorruptRegion(offset=pos, length=end - pos, reason=_corrupt_reason(data, pos))
            )
            if nxt < 0:
                break
            pos = nxt
            continue

        page_no, page_size, payload_len, end = candidate
        payload = data[pos + HEADER_SIZE : end]
        try:
            records = decode_records(payload)
        except PageError as exc:
            result.corrupt_regions.append(
                CorruptRegion(offset=pos, length=end - pos, reason="malformed records: %s" % exc)
            )
            pos = end
            continue

        result.valid_pages.append(
            ValidPage(
                page_no=page_no,
                offset=pos,
                page_size=page_size,
                payload_len=payload_len,
                record_count=len(records),
            )
        )
        rec_pos = pos + HEADER_SIZE
        for record_index, (key, value) in enumerate(records):
            occurrences.setdefault(key, []).append(
                Occurrence(
                    key=key,
                    value=value,
                    page_no=page_no,
                    page_offset=pos,
                    record_index=record_index,
                    record_offset=rec_pos,
                )
            )
            rec_pos += 2 + len(key) + 4 + len(value)
        pos = end

    result.index = _build_index(occurrences, result.conflicts)
    return result


def _corrupt_reason(data, pos):
    if data[pos : pos + 4] != MAGIC:
        return "missing page magic"
    if pos + HEADER_SIZE > len(data):
        return "truncated page header"
    candidate = header_candidate(data, pos)
    if candidate is None:
        page_no, page_size, payload_len = struct.unpack_from(">QII", data, pos + 4)
        if pos + HEADER_SIZE + payload_len > len(data):
            return "truncated page payload"
        return "crc32 mismatch"
    return "unknown"


def _build_index(occurrences, conflicts_out):
    """Resolve duplicates: largest page number wins; same page -> later record."""
    entries = {}
    for key, occs in occurrences.items():
        winner = max(occs, key=lambda o: (o.page_no, o.record_index))
        losers = [o for o in occs if o is not winner]
        if losers:
            losers.sort(key=lambda o: (o.page_no, o.record_index))
            conflicts_out.append(Conflict(key=key, winner=winner, losers=losers))
        entries[key] = IndexEntry(
            key=key,
            value=winner.value,
            page_no=winner.page_no,
            page_offset=winner.page_offset,
            record_offset=winner.record_offset,
        )
    conflicts_out.sort(key=lambda c: c.key)
    return RebuiltIndex(entries)


def rebuild_index(path):
    """Convenience wrapper: scan ``path`` and return the :class:`RebuiltIndex`."""
    return scan_file(path).index
