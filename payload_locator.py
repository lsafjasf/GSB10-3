#!/usr/bin/env python3
"""Locate, score and extract archive payloads hidden behind an executable shell.

The analyser is purely static: it never executes the inspected file.
Payloads are located by combining two independent signals:

1. signature match -- a magic byte string must be present;
2. structural self-consistency -- header fields, CRCs and declared
   lengths must agree with the actual bytes on disk.

Every signature hit becomes a Candidate.  Candidates are scored by
structural integrity and the full ranked list is reported, together with
the reason each rejected candidate was dismissed.  Padding of arbitrary
length between shell and payload is tolerated because the scan walks the
whole file; the reported offset is always the real one.

Scoring (GSPK container, max 100):
    +10  magic signature present
    +10  supported format version
    +10  header-size field matches the layout
    +05  reserved header bytes are zero
    +15  header CRC32 matches
    +20  declared payload length fits inside the file
    +30  payload CRC32 matches

Scoring (ZIP container, max 100):
    +10  local-file-header signature present
    +15  local header fields are plausible
    +25  central directory parses
    +50  every member passes its CRC check

GSPK header layout (32 bytes, little-endian):
    0  4s  magic "GSPK"
    4  H   format version (1)
    6  H   header size in bytes (32)
    8  I   payload length
    12 I   payload CRC32
    16 I   header CRC32 over bytes 0..15
    20 12s reserved, must be zero
"""

from __future__ import annotations

import argparse
import binascii
import io
import json
import struct
import sys
import zipfile
from dataclasses import asdict, dataclass, field

GSPK_MAGIC = b"GSPK"
GSPK_VERSION = 1
GSPK_HEADER_SIZE = 32
GSPK_RESERVED_SIZE = 12
GSPK_STRUCT = struct.Struct("<4sHHIII")

ZIP_MAGIC = b"PK\x03\x04"
ZIP_EOCD = b"PK\x05\x06"
ZIP_LOCAL_STRUCT = struct.Struct("<IHHHHHIIIHH")
ZIP_PLAUSIBLE_METHODS = {0, 8, 9, 12, 14, 95, 98, 99}

STATUS_VALID = "valid"
STATUS_TRUNCATED = "truncated"
STATUS_REJECTED = "rejected"

VALID_THRESHOLD = 100


@dataclass
class Candidate:
    format: str
    offset: int
    score: int
    status: str
    reasons: list = field(default_factory=list)
    payload_offset: int = None
    payload_length: int = None
    detail: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def crc32(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def build_gspk(payload: bytes) -> bytes:
    """Pack payload into a GSPK container (used by the sample generator)."""
    head = GSPK_STRUCT.pack(
        GSPK_MAGIC, GSPK_VERSION, GSPK_HEADER_SIZE,
        len(payload), crc32(payload), 0,
    )
    hcrc = crc32(head[:16])
    head = GSPK_STRUCT.pack(
        GSPK_MAGIC, GSPK_VERSION, GSPK_HEADER_SIZE,
        len(payload), crc32(payload), hcrc,
    )
    return head + b"\x00" * GSPK_RESERVED_SIZE + payload


def _find_all(data: bytes, needle: bytes):
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx < 0:
            return
        yield idx
        start = idx + 1


def _eval_gspk(data: bytes, off: int) -> Candidate:
    reasons = ["magic signature matched"]
    score = 10
    remaining = len(data) - off
    if remaining < GSPK_HEADER_SIZE:
        reasons.append(
            "header truncated: only %d of %d header bytes present"
            % (remaining, GSPK_HEADER_SIZE)
        )
        return Candidate("gspk", off, score, STATUS_REJECTED, reasons)

    header = data[off:off + GSPK_HEADER_SIZE]
    magic, version, hsize, plen, pcrc, hcrc = GSPK_STRUCT.unpack(
        header[:GSPK_STRUCT.size]
    )
    reserved = header[GSPK_STRUCT.size:]

    if version == GSPK_VERSION:
        score += 10
    else:
        reasons.append("unsupported format version %d (expected %d)"
                       % (version, GSPK_VERSION))

    if hsize == GSPK_HEADER_SIZE:
        score += 10
    else:
        reasons.append("header-size field is %d, layout requires %d"
                       % (hsize, GSPK_HEADER_SIZE))

    if reserved == b"\x00" * GSPK_RESERVED_SIZE:
        score += 5
    else:
        reasons.append("reserved header bytes are not zero")

    header_crc_ok = crc32(header[:16]) == hcrc
    if header_crc_ok:
        score += 15
    else:
        reasons.append(
            "header CRC32 mismatch (stored 0x%08x, computed 0x%08x): "
            "signature is coincidental or the header was tampered with"
            % (hcrc, crc32(header[:16]))
        )

    poff = off + hsize if 0 < hsize <= remaining else off + GSPK_HEADER_SIZE
    avail = len(data) - poff
    fits = plen <= avail
    if fits:
        score += 20
    else:
        reasons.append(
            "payload truncated: header declares %d bytes but only %d "
            "remain in the file" % (plen, avail)
        )

    payload_crc_ok = False
    if fits:
        payload_crc_ok = crc32(data[poff:poff + plen]) == pcrc
        if payload_crc_ok:
            score += 30
        else:
            reasons.append(
                "payload CRC32 mismatch (stored 0x%08x, computed 0x%08x): "
                "payload corrupted or signature coincidental"
                % (pcrc, crc32(data[poff:poff + plen]))
            )

    if header_crc_ok and fits and payload_crc_ok:
        status = STATUS_VALID
    elif header_crc_ok and not fits:
        status = STATUS_TRUNCATED
        reasons.append("header is self-consistent, so this is a real "
                       "container whose payload was cut short")
    else:
        status = STATUS_REJECTED

    return Candidate(
        "gspk", off, score, status, reasons,
        payload_offset=poff, payload_length=plen,
        detail={
            "version": version,
            "header_size": hsize,
            "header_crc_ok": header_crc_ok,
            "payload_crc_ok": payload_crc_ok if fits else None,
            "payload_available": avail,
        },
    )


def _eval_zip(data: bytes, off: int) -> Candidate:
    reasons = ["local-file-header signature matched"]
    score = 10
    tail = data[off:]

    plausible = False
    if len(tail) >= ZIP_LOCAL_STRUCT.size:
        (sig, ver, flags, method, mtime, mdate, crc, csize, usize,
         nlen, elen) = ZIP_LOCAL_STRUCT.unpack(tail[:ZIP_LOCAL_STRUCT.size])
        name_region = tail[ZIP_LOCAL_STRUCT.size:
                           ZIP_LOCAL_STRUCT.size + nlen + elen]
        plausible = (
            method in ZIP_PLAUSIBLE_METHODS
            and 0 < nlen <= 4096
            and len(name_region) == nlen + elen
            and csize <= len(tail) + 65536
            and usize <= 1 << 40
        )
    if plausible:
        score += 15
    else:
        reasons.append(
            "local header fields implausible (bad method/name-length/"
            "sizes): signature is coincidental data, not an archive"
        )
        return Candidate("zip", off, score, STATUS_REJECTED, reasons)

    try:
        zf = zipfile.ZipFile(io.BytesIO(tail))
        names = zf.namelist()
    except (ValueError, OSError) as exc:
        reasons.append(
            "central directory unreadable from this offset (%s): this is a "
            "local header inside archive data, not the archive start" % exc)
        return Candidate("zip", off, score, STATUS_REJECTED, reasons)
    except zipfile.BadZipFile:
        if ZIP_EOCD not in tail:
            reasons.append(
                "local header is plausible but no end-of-central-directory "
                "record exists before EOF: archive truncated"
            )
            return Candidate("zip", off, score, STATUS_TRUNCATED, reasons)
        reasons.append("central directory present but unparseable")
        return Candidate("zip", off, score, STATUS_REJECTED, reasons)

    score += 25
    try:
        bad = zf.testzip()
    except (ValueError, OSError) as exc:
        reasons.append(
            "central directory lists members but their local headers are "
            "not reachable from this offset (%s): signature sits inside "
            "archive data rather than at the archive start" % exc)
        return Candidate("zip", off, score, STATUS_REJECTED, reasons)
    if bad is None:
        score += 50
        status = STATUS_VALID
        reasons.append("central directory parses and all %d member(s) pass "
                       "their CRC checks" % len(names))
    else:
        status = STATUS_REJECTED
        reasons.append("member %r fails its CRC check: archive corrupt "
                       "or signature coincidental" % bad)

    return Candidate(
        "zip", off, score, status, reasons,
        payload_offset=off, payload_length=len(tail),
        detail={"members": names},
    )


FORMATS = {
    "gspk": (GSPK_MAGIC, _eval_gspk),
    "zip": (ZIP_MAGIC, _eval_zip),
}


def scan(data: bytes, formats=None) -> list:
    """Scan data for payload candidates.

    Returns every signature hit as a Candidate, sorted by descending
    structural-integrity score (ties broken by offset).
    """
    names = formats or sorted(FORMATS)
    candidates = []
    for name in names:
        magic, evaluator = FORMATS[name]
        for off in _find_all(data, magic):
            candidates.append(evaluator(data, off))
    candidates.sort(key=lambda c: (-c.score, c.offset))
    return candidates


def scan_file(path: str, formats=None) -> list:
    with open(path, "rb") as fh:
        return scan(fh.read(), formats)


def best_valid(candidates):
    for cand in candidates:
        if cand.status == STATUS_VALID:
            return cand
    return None


def extract_payload(data: bytes, cand: Candidate) -> bytes:
    """Return the raw payload bytes for a valid/truncated candidate."""
    if cand.format == "gspk":
        end = min(len(data), cand.payload_offset + cand.payload_length)
        return data[cand.payload_offset:end]
    if cand.format == "zip":
        return data[cand.offset:]
    raise ValueError("unknown format %r" % cand.format)


def _format_report(candidates) -> str:
    lines = []
    if not candidates:
        lines.append("no payload signature found anywhere in the file")
        return "\n".join(lines)
    lines.append("%-3s %-10s %-6s %-9s %-5s %s"
                 % ("#", "offset", "format", "status", "score", "assessment"))
    for idx, cand in enumerate(candidates):
        first = cand.reasons[0] if cand.reasons else ""
        lines.append("%-3d %-10d %-6s %-9s %-5d %s"
                     % (idx, cand.offset, cand.format, cand.status,
                        cand.score, first))
        for reason in cand.reasons[1:]:
            lines.append("%-33s %s" % ("", reason))
    valid = best_valid(candidates)
    if valid is not None:
        lines.append("verdict: payload at offset %d (%s), %d payload bytes"
                     % (valid.offset, valid.format, valid.payload_length))
    else:
        lines.append("verdict: no intact payload could be confirmed")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Statically locate and verify archive payloads hidden "
                    "behind an executable shell. Never executes the file.")
    parser.add_argument("file", help="file to analyse")
    parser.add_argument("--format", choices=sorted(FORMATS) + ["all"],
                        default="all", help="container format to look for")
    parser.add_argument("--json", action="store_true",
                        help="emit the full candidate list as JSON")
    parser.add_argument("--extract", type=int, metavar="OFFSET",
                        help="extract the payload of the candidate at OFFSET")
    parser.add_argument("-o", "--output", help="output path for --extract")
    args = parser.parse_args(argv)

    formats = None if args.format == "all" else [args.format]
    with open(args.file, "rb") as fh:
        data = fh.read()
    candidates = scan(data, formats)

    if args.json:
        print(json.dumps([c.to_dict() for c in candidates],
                         indent=2, sort_keys=True))
    else:
        print(_format_report(candidates))

    if args.extract is not None:
        match = [c for c in candidates if c.offset == args.extract]
        if not match:
            print("no candidate at offset %d" % args.extract, file=sys.stderr)
            return 2
        cand = match[0]
        if cand.status == STATUS_REJECTED:
            print("candidate at offset %d is rejected; refusing to extract"
                  % args.extract, file=sys.stderr)
            return 2
        payload = extract_payload(data, cand)
        out = args.output or (args.file + ".payload")
        with open(out, "wb") as fh:
            fh.write(payload)
        print("wrote %d payload bytes to %s" % (len(payload), out))

    return 0 if best_valid(candidates) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
