#!/usr/bin/env python3
"""Generate sample archives for payload_locator.py (standard library only).

The "executable shell" is inert stub bytes -- a script banner followed by
pseudo-random bytes; the analyser must never run it and neither does this
generator.
"""

from __future__ import annotations

import os
import random
import zipfile

from payload_locator import GSPK_MAGIC, build_gspk

SHELL_BANNER = (
    b"#!/bin/sh\n# self-extracting archive stub (do not execute samples)\n"
    b"echo extracting...; exit 0\n"
)

PAYLOAD = (
    b"ARCHIVE-PAYLOAD-v1\n"
    + bytes(range(256)) * 8
    + b"\nsecret-archive-content\n"
)


FORBIDDEN = (GSPK_MAGIC, b"PK\x03\x04")


def safe_random(rng: random.Random, size: int) -> bytes:
    """Random bytes guaranteed not to contain either payload signature."""
    data = bytearray(rng.randrange(256) for _ in range(size))
    for magic in FORBIDDEN:
        while True:
            idx = data.find(magic)
            if idx < 0:
                break
            for j in range(idx, idx + len(magic)):
                data[j] = rng.randrange(256)
    return bytes(data)


def shell_stub(seed: int, size: int = 2048) -> bytes:
    return SHELL_BANNER + safe_random(random.Random(seed), size)


def padding(length: int, seed: int = 0) -> bytes:
    return safe_random(random.Random(seed), length)


def _write(path: str, data: bytes) -> str:
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def gspk_ok(outdir: str) -> str:
    return _write(os.path.join(outdir, "ok.gspk.bin"),
                  shell_stub(1) + build_gspk(PAYLOAD))


def gspk_padded(outdir: str) -> str:
    data = shell_stub(2, 512) + padding(5009, 20) + build_gspk(PAYLOAD)
    return _write(os.path.join(outdir, "padded.gspk.bin"), data)


def no_payload(outdir: str) -> str:
    return _write(os.path.join(outdir, "no_payload.bin"), shell_stub(3, 4096))


def gspk_truncated(outdir: str) -> str:
    packed = build_gspk(PAYLOAD)
    return _write(os.path.join(outdir, "truncated.gspk.bin"),
                  shell_stub(4) + packed[:len(packed) // 3])


def gspk_sig_inside_data(outdir: str) -> str:
    # The magic lives inside random shell/data bytes; a real payload follows.
    blob = shell_stub(5, 256)
    decoy = GSPK_MAGIC + b"\x99\x88\x77\x66" * 8 + safe_random(random.Random(55), 32)
    blob = blob[:100] + decoy + blob[100 + len(decoy):]
    return _write(os.path.join(outdir, "sig_inside_data.gspk.bin"),
                  blob + padding(17, 51) + build_gspk(PAYLOAD))


def gspk_tampered_shell(outdir: str) -> str:
    data = bytearray(shell_stub(6) + padding(31, 61) + build_gspk(PAYLOAD))
    data[3] ^= 0xFF
    data[400] ^= 0x01
    return _write(os.path.join(outdir, "tampered_shell.gspk.bin"), bytes(data))


def gspk_tampered_payload(outdir: str) -> str:
    data = bytearray(shell_stub(7) + build_gspk(PAYLOAD))
    payload_start = len(data) - 10
    data[payload_start] ^= 0xFF
    return _write(os.path.join(outdir, "tampered_payload.gspk.bin"), bytes(data))


def gspk_decoy_header(outdir: str) -> str:
    # Decoy with a magic AND a valid header CRC, but a wrong payload CRC:
    # header-level checks pass, only payload verification can expose it.
    decoy = bytearray(build_gspk(b"this is not the payload you want"))
    decoy[-3] ^= 0xAA
    return _write(os.path.join(outdir, "decoy_header.gspk.bin"),
                  shell_stub(8) + bytes(decoy)
                  + padding(40, 81) + build_gspk(PAYLOAD))


def gspk_multi(outdir: str) -> str:
    rng = random.Random(91)
    decoy_a = GSPK_MAGIC + safe_random(rng, 40)
    decoy_b = GSPK_MAGIC + b"\x01\x00" + b"\x20\x00" + safe_random(rng, 36)
    data = (shell_stub(9) + decoy_a + padding(100, 92)
            + decoy_b + padding(200, 93) + build_gspk(PAYLOAD))
    return _write(os.path.join(outdir, "multi_candidates.gspk.bin"), data)


def gspk_partial_header(outdir: str) -> str:
    # Magic at EOF with fewer than 32 header bytes following.
    return _write(os.path.join(outdir, "partial_header.gspk.bin"),
                  shell_stub(10, 300) + GSPK_MAGIC + b"\x01\x00\x20")


def zip_sfx(outdir: str) -> str:
    path = os.path.join(outdir, "sfx.zip.bin")
    with open(path, "wb") as fh:
        fh.write(shell_stub(11) + padding(233, 111))
        with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("readme.txt", "hello from the zip payload\n")
            zf.writestr("data/blob.bin", PAYLOAD)
    return path


def zip_sig_inside_data(outdir: str) -> str:
    # ZIP magic appearing in random data with no actual archive behind it.
    rng = random.Random(121)
    blob = (shell_stub(12, 256) + b"PK\x03\x04"
            + safe_random(rng, 60))
    return _write(os.path.join(outdir, "sig_inside_data.zip.bin"), blob)


def zip_truncated(outdir: str) -> str:
    path = os.path.join(outdir, "truncated.zip.bin")
    buf_path = os.path.join(outdir, "_tmp_full.zip")
    with zipfile.ZipFile(buf_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", "truncated archive\n" * 100)
        zf.writestr("data/blob.bin", PAYLOAD * 4)
    with open(buf_path, "rb") as fh:
        archive = fh.read()
    os.unlink(buf_path)
    return _write(path, shell_stub(13) + archive[:len(archive) // 2])


SAMPLE_BUILDERS = [
    ("ok", gspk_ok),
    ("padded", gspk_padded),
    ("no_payload", no_payload),
    ("truncated_gspk", gspk_truncated),
    ("sig_inside_gspk", gspk_sig_inside_data),
    ("tampered_shell", gspk_tampered_shell),
    ("tampered_payload", gspk_tampered_payload),
    ("decoy_header", gspk_decoy_header),
    ("multi", gspk_multi),
    ("partial_header", gspk_partial_header),
    ("zip_sfx", zip_sfx),
    ("zip_sig_inside", zip_sig_inside_data),
    ("zip_truncated", zip_truncated),
]


def build_all(outdir: str):
    os.makedirs(outdir, exist_ok=True)
    return {name: builder(outdir) for name, builder in SAMPLE_BUILDERS}


def main() -> None:
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
    paths = build_all(outdir)
    print("wrote %d samples to %s" % (len(paths), outdir))
    for name, path in sorted(paths.items()):
        print("  %-16s %8d bytes  %s" % (name, os.path.getsize(path), path))
    print("expected payload (%d bytes): PAYLOAD constant in make_samples.py"
          % len(PAYLOAD))


if __name__ == "__main__":
    main()
