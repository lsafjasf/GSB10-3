"""Convert a PGM to PNG (for visual inspection). Standard library only.

usage: python3 pgm2png.py in.pgm out.png
"""

import struct
import sys
import zlib

from imagerotate import read_pgm


def pgm_to_png(src, dst):
    image = read_pgm(src)
    raw = b"".join(
        b"\x00" + bytes(min(max(int(round(v)), 0), 255) for v in row)
        for row in image.data)

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB",
                                        image.width, image.height,
                                        8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    with open(dst, "wb") as handle:
        handle.write(png)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python3 pgm2png.py in.pgm out.png")
    pgm_to_png(sys.argv[1], sys.argv[2])
