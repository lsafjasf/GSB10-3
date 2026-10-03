"""Locate and unpack a payload appended behind an executable stub.

Safety contract
---------------
The input file is treated strictly as opaque bytes. This module never makes
the file executable, never spawns it, and never calls exec*/eval on its
contents. Only ``open(path, 'rb')`` plus pure byte parsing is used.

Archive layout (little-endian)::

    [ shell / stub ][ arbitrary padding ][ HEADER ][ payload ][ FOOTER ]

    HEADER = MAGIC + u8 version(=1) + u8 flags(=0) + u16 header_len(=48)
             + u64 payload_len + u32 payload_crc32 + 32x u8 prefix_sha256
    FOOTER = MAGIC + u64 payload_len(echo) + u32 payload_crc32(echo)

The same MAGIC may legitimately occur inside the payload; such hits are kept
as candidates but fail the structural checks and are rejected with reasons.
"""

import argparse
import hashlib
import json
import struct
import sys
import zlib

MAGIC = b"==PXAR/PAYLOAD/v1=="
MAGIC_LEN = len(MAGIC)
FORMAT_VERSION = 1
HEADER_LEN = 48
_FIXED = struct.Struct("<BBHQI")          # version, flags, header_len, len, crc
_FIXED_SIZE = _FIXED.size                 # 16
_FOOTER_TAIL = struct.Struct("<QI")       # payload_len, crc32
FOOTER_LEN = MAGIC_LEN + _FOOTER_TAIL.size

# Score weights. They sum to 100. The "hard" checks are mandatory for a
# candidate to be considered a real, extractable payload; everything else
# (currently only the prefix hash) is advisory.
WEIGHTS = {
    "signature": 10,
    "header_complete": 8,
    "version": 7,
    "flags": 5,
    "header_len": 5,
    "bounds": 20,
    "footer": 15,
    "crc32": 25,
    "prefix_sha256": 5,
}
HARD_CHECKS = {
    "header_complete",
    "version",
    "flags",
    "header_len",
    "bounds",
    "footer",
    "crc32",
}
MAX_SCORE = sum(WEIGHTS.values())


class PayloadError(Exception):
    """Raised when no structurally valid payload can be located."""


def build_archive(shell, payload, padding=b""):
    """Construct a well-formed archive (used by the generator and tests)."""
    if not isinstance(shell, (bytes, bytearray)):
        raise TypeError("shell must be bytes")
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    if not isinstance(padding, (bytes, bytearray)):
        raise TypeError("padding must be bytes")
    prefix = bytes(shell) + bytes(padding)
    digest = hashlib.sha256(prefix).digest()
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = (
        MAGIC
        + _FIXED.pack(FORMAT_VERSION, 0, HEADER_LEN, len(payload), crc)
        + digest
    )
    footer = MAGIC + _FOOTER_TAIL.pack(len(payload), crc)
    return prefix + header + bytes(payload) + footer


def _check(name, passed, detail):
    return {
        "name": name,
        "passed": bool(passed),
        "weight": WEIGHTS[name],
        "detail": detail,
    }


def _score_candidate(data, offset):
    """Evaluate one MAGIC hit. Never raises; records every failure reason."""
    size = len(data)
    checks = []
    failures = []
    warnings = []

    checks.append(_check(
        "signature", True,
        "magic %r found at offset %d (0x%x)" % (MAGIC, offset, offset),
    ))

    header_start = offset + MAGIC_LEN
    payload_start = header_start + HEADER_LEN
    have_header = header_start + HEADER_LEN <= size
    checks.append(_check(
        "header_complete", have_header,
        "fixed header needs %d bytes at 0x%x; file is %d bytes"
        % (HEADER_LEN, header_start, size),
    ))

    version = flags = header_len = payload_len = crc = None
    prefix_digest = None
    if have_header:
        version, flags, header_len, payload_len, crc = _FIXED.unpack_from(
            data, header_start
        )
        prefix_digest = bytes(data[payload_start - 32:payload_start])

    if have_header:
        ok = version == FORMAT_VERSION
        checks.append(_check(
            "version", ok,
            "declared version=%r, supported=%r" % (version, FORMAT_VERSION),
        ))
        ok = flags == 0
        checks.append(_check(
            "flags", ok,
            "reserved flags byte=0x%02x (only 0x00 is defined)" % flags,
        ))
        ok = header_len == HEADER_LEN
        checks.append(_check(
            "header_len", ok,
            "declared header_len=%d, expected=%d"
            % (header_len, HEADER_LEN),
        ))
    else:
        for name in ("version", "flags", "header_len"):
            checks.append(_check(name, False, "header truncated; not parsed"))

    payload_end = payload_start + (payload_len or 0)
    footer_at = payload_end
    bounds_ok = have_header and payload_end + FOOTER_LEN <= size
    if have_header:
        checks.append(_check(
            "bounds", bounds_ok,
            "payload_len=%d would end at 0x%x and need a %d-byte footer; "
            "file ends at 0x%x (%d bytes %s)"
            % (payload_len, payload_end, FOOTER_LEN, size, size,
               "available" if bounds_ok else "short"),
        ))
    else:
        checks.append(_check("bounds", False, "header truncated; no length"))

    footer_ok = False
    if bounds_ok:
        fmagic = bytes(data[footer_at:footer_at + MAGIC_LEN])
        flen, fcrc = _FOOTER_TAIL.unpack_from(data, footer_at + MAGIC_LEN)
        reasons = []
        if fmagic != MAGIC:
            reasons.append("footer magic is %r" % fmagic)
        if flen != payload_len:
            reasons.append("footer length=%d != header length=%d"
                           % (flen, payload_len))
        if fcrc != crc:
            reasons.append("footer crc=0x%08x != header crc=0x%08x"
                           % (fcrc, crc or 0))
        footer_ok = not reasons
        checks.append(_check(
            "footer", footer_ok,
            "; ".join(reasons) if reasons
            else "footer magic and echoed length/crc match at 0x%x"
                 % footer_at,
        ))
    else:
        checks.append(_check(
            "footer", False,
            "cannot reach footer at 0x%x (bounds check failed)" % footer_at,
        ))

    crc_ok = False
    if bounds_ok:
        actual = zlib.crc32(bytes(data[payload_start:payload_end])) & 0xFFFFFFFF
        crc_ok = actual == crc
        checks.append(_check(
            "crc32", crc_ok,
            "stored=0x%08x computed=0x%08x over %d payload bytes"
            % (crc or 0, actual, payload_len),
        ))
    else:
        checks.append(_check(
            "crc32", False,
            "full payload unavailable; checksum not verifiable",
        ))

    if have_header:
        actual_prefix = hashlib.sha256(bytes(data[:offset])).digest()
        prefix_ok = actual_prefix == prefix_digest
        checks.append(_check(
            "prefix_sha256", prefix_ok,
            "stored=%s computed=%s over %d prefix bytes (shell + padding)"
            % (prefix_digest.hex(), actual_prefix.hex(), offset),
        ))
        if not prefix_ok:
            warnings.append(
                "prefix (shell/padding) was modified after archiving; "
                "payload itself is still authenticated by crc32 + footer"
            )
    else:
        checks.append(_check(
            "prefix_sha256", False, "header truncated; digest unavailable",
        ))

    for chk in checks:
        if not chk["passed"] and chk["name"] in HARD_CHECKS:
            failures.append("%s: %s" % (chk["name"], chk["detail"]))

    score = sum(c["weight"] for c in checks if c["passed"])
    return {
        "offset": offset,
        "score": score,
        "max_score": MAX_SCORE,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "extractable": not failures,
        "payload_offset": payload_start if bounds_ok else None,
        "payload_len": payload_len if bounds_ok else None,
        "payload_end": payload_end if bounds_ok else None,
    }


def find_candidates(data):
    """Return every MAGIC hit, scored by structural self-consistency.

    Overlapping occurrences are scanned so an embedded signature cannot hide
    a later one. Candidates are ranked by score (desc), then offset (asc).
    """
    candidates = []
    start = 0
    while True:
        offset = data.find(MAGIC, start)
        if offset < 0:
            break
        candidates.append(_score_candidate(data, offset))
        start = offset + 1
    candidates.sort(key=lambda c: (-c["score"], c["offset"]))
    for rank, cand in enumerate(candidates, 1):
        cand["rank"] = rank
    return candidates


def analyze(data):
    """Analyze raw bytes; never executes anything. Pure function."""
    candidates = find_candidates(data)
    best = candidates[0] if candidates else None
    if best is None:
        status = "no_candidate"
    elif best["extractable"]:
        status = ("valid" if best["score"] == MAX_SCORE
                  else "valid_shell_modified")
    else:
        status = "invalid_candidates"
    return {
        "file_size": len(data),
        "status": status,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "best": best,
        "payload": (
            {
                "offset": best["payload_offset"],
                "length": best["payload_len"],
                "end": best["payload_end"],
                "crc32": next(
                    c["detail"] for c in best["checks"] if c["name"] == "crc32"
                ),
            }
            if best and best["extractable"] else None
        ),
    }


def analyze_file(path):
    with open(path, "rb") as fh:
        return analyze(fh.read())


def extract(data):
    """Return (payload_offset, payload_bytes) or raise PayloadError."""
    result = analyze(data)
    best = result["best"]
    if best is None:
        raise PayloadError("no signature %r found in %d bytes"
                           % (MAGIC, result["file_size"]))
    if not best["extractable"]:
        raise PayloadError(
            "candidate at offset 0x%x scored %d/%d but failed structural "
            "checks: %s" % (best["offset"], best["score"], MAX_SCORE,
                            "; ".join(best["failures"]))
        )
    start, end = best["payload_offset"], best["payload_end"]
    return start, bytes(data[start:end])


def _render_human(result):
    lines = [
        "file size : %d bytes" % result["file_size"],
        "status    : %s" % result["status"],
        "candidates: %d (all listed, best first)" % result["candidate_count"],
    ]
    for cand in result["candidates"]:
        lines.append("")
        lines.append("  #%d offset=0x%x score=%d/%d extractable=%s"
                     % (cand["rank"], cand["offset"], cand["score"],
                        cand["max_score"], cand["extractable"]))
        for chk in cand["checks"]:
            mark = "+" if chk["passed"] else "-"
            lines.append("    %s %-14s (w%2d) %s"
                         % (mark, chk["name"], chk["weight"], chk["detail"]))
        for reason in cand["failures"]:
            lines.append("    ! REJECT: %s" % reason)
        for warning in cand["warnings"]:
            lines.append("    ? WARNING: %s" % warning)
    info = result["payload"]
    if info:
        lines.append("")
        lines.append("payload: offset=0x%x length=%d end=0x%x"
                     % (info["offset"], info["length"], info["end"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Locate/verify an appended payload without executing the "
                    "stub. Read-only; no code from the input is ever run.")
    parser.add_argument("file", help="archive to analyze (use - for stdin)")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable candidate scoring")
    parser.add_argument("--extract", metavar="OUT",
                        help="write the authenticated payload to OUT")
    args = parser.parse_args(argv)

    if args.file == "-":
        data = sys.stdin.buffer.read()
    else:
        with open(args.file, "rb") as fh:
            data = fh.read()

    result = analyze(data)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(_render_human(result))

    if args.extract:
        try:
            offset, payload = extract(data)
        except PayloadError as exc:
            print("extraction refused: %s" % exc, file=sys.stderr)
            return 3
        with open(args.extract, "wb") as fh:
            fh.write(payload)
        print("wrote %d payload bytes from offset 0x%x to %s"
              % (len(payload), offset, args.extract), file=sys.stderr)

    if result["status"] == "no_candidate":
        return 2
    if result["status"] == "invalid_candidates":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
