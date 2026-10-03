"""Generate boundary-case sample archives and a candidate-scoring report.

The samples deliberately include fake/mutated/truncated cases so the
locator's signature + structural-consistency double check can be exercised.

Usage:
    python3 make_samples.py [outdir]            # default: ./samples
"""

import json
import os
import random
import sys

import unpacker

SHELL = (
    b"#!/bin/sh\n"
    b"# self-extracting demo stub; a real one would tail+exec itself.\n"
    b'echo "running stub (the unpacker never does this)" >/dev/null\n'
    b"exit 0\n"
)

PAYLOAD = (
    b"hello payload line 1\n"
    + bytes(range(256)) * 4
    + b"\x00\x00\xff\xfe trailer\n"
)


def write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


def main(outdir="samples"):
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(107)
    manifest = {}

    def add(name, data, note, payload_expect=None):
        n = write(os.path.join(outdir, name), data)
        result = unpacker.analyze(data)
        manifest[name] = {
            "note": note,
            "size": n,
            "expected_status": result["status"],
            "candidate_count": result["candidate_count"],
            "candidates": [
                {
                    "rank": c["rank"],
                    "offset": c["offset"],
                    "score": c["score"],
                    "max_score": c["max_score"],
                    "extractable": c["extractable"],
                    "hard_failures": c["failures"],
                    "warnings": c["warnings"],
                }
                for c in result["candidates"]
            ],
        }
        if payload_expect is not None:
            manifest[name]["payload_offset_expected"] = payload_expect

    # 1. clean archive, no padding
    arc = unpacker.build_archive(SHELL, PAYLOAD)
    add("01_clean.bin", arc, "well-formed archive, zero padding",
        payload_expect=len(SHELL) + unpacker.HEADER_LEN + unpacker.MAGIC_LEN)

    # 2. long arbitrary padding between shell and payload
    pad = bytes(rng.randrange(256) for _ in range(4096))
    arc = unpacker.build_archive(SHELL, PAYLOAD, pad)
    add("02_long_padding.bin", arc, "4096 random-byte padding before header",
        payload_expect=len(SHELL) + 4096
        + unpacker.HEADER_LEN + unpacker.MAGIC_LEN)

    # 3. magic embedded inside otherwise-random data -> false positive
    junk = bytes(rng.randrange(256) for _ in range(2000))
    pos = 512
    fake = junk[:pos] + unpacker.MAGIC + junk[pos + unpacker.MAGIC_LEN:]
    add("03_embedded_sig_only.bin", fake,
        "signature appears inside random data; no valid structure follows")

    # 4. magic inside the payload body as well (second hit)
    inner_pad = b"\x00" * 8
    body = (b"pre-sig bytes " + unpacker.MAGIC + b" post-sig bytes" * 64)
    arc = unpacker.build_archive(SHELL, body, inner_pad)
    add("04_sig_inside_payload.bin", arc,
        "real archive whose payload also contains the magic string")

    # 5. truncated payload (cut 17 bytes before the footer should end)
    arc = unpacker.build_archive(SHELL, PAYLOAD, b"  " * 16)
    cut = len(arc) - 17
    add("05_truncated.bin", arc[:cut],
        "well-formed archive cut 17 bytes early (payload+footer truncated)")

    # 6. truncated exactly in the header (only magic + a few header bytes)
    arc = unpacker.build_archive(SHELL, PAYLOAD)
    hdr_off = arc.find(unpacker.MAGIC)
    add("06_header_truncated.bin", arc[:hdr_off + unpacker.MAGIC_LEN + 6],
        "file ends 6 bytes after the magic, header incomplete")

    # 7. tampered shell (payload still authentic; prefix hash warns)
    arc = bytearray(unpacker.build_archive(SHELL, PAYLOAD, b"\xaa" * 32))
    arc[2] ^= 0x01
    add("07_shell_tampered.bin", bytes(arc),
        "one bit flipped inside the shell before the payload")

    # 8. tampered payload (crc32 + footer echo reject it)
    arc = bytearray(unpacker.build_archive(SHELL, PAYLOAD))
    pstart, pbytes = unpacker.extract(bytes(arc))
    arc[pstart + 10] ^= 0xFF
    add("08_payload_tampered.bin", bytes(arc),
        "one byte flipped inside the payload after archiving")

    # 9. empty payload is legal
    arc = unpacker.build_archive(SHELL, b"", b"\x00" * 5)
    add("09_empty_payload.bin", arc, "zero-length payload with padding",
        payload_expect=len(SHELL) + 5
        + unpacker.HEADER_LEN + unpacker.MAGIC_LEN)

    # 10. no payload at all
    add("10_no_payload.bin", SHELL + b"# end of plain script\n",
        "plain executable stub with no appended archive")

    # 11. two archives concatenated -> two valid candidates, both reported
    arc1 = unpacker.build_archive(SHELL, b"FIRST-PAYLOAD-DATA" * 8, b"\xcc")
    arc2 = unpacker.build_archive(b"#second stub\n", b"SECOND-PAYLOAD-DATA" * 8)
    add("11_double_archive.bin", arc1 + arc2,
        "two complete archives concatenated; both candidates listed")

    # 12. decoy signature + real archive after padding
    decoy = SHELL + unpacker.MAGIC + b"not-a-header!!" * 4
    real = unpacker.build_archive(b"", PAYLOAD, bytes(rng.getrandbits(8)
                                       for _ in range(128)))
    add("12_decoy_then_real.bin", decoy + real,
        "fake signature with garbage header, then a genuine archive")

    with open(os.path.join(outdir, "scoring_report.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, sort_keys=True)

    print("generated %d samples in %s/" % (len(manifest), outdir))
    for name, info in sorted(manifest.items()):
        scores = ", ".join("#%d@0x%x=%d/%d%s" % (
            c["rank"], c["offset"], c["score"], c["max_score"],
            "" if c["extractable"] else "(rejected)")
            for c in info["candidates"]) or "no candidates"
        print("  %-28s %-22s %s" % (name, info["expected_status"], scores))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "samples"))
