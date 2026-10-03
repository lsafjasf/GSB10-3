"""测试夹具：用标准库手工构造 BMP-DIB / PNG 载荷与 IconFile 实例。

不依赖任何图像库：BMP 逐行打包像素，PNG 用 zlib 压缩原始扫描线。
"""

import struct
import zlib

from ico import IconFile, IconImage


def build_bmp_blob(width, height, bpp=32, alpha=True):
    """构造 ICO 内嵌的 BMP-DIB（无文件头，biHeight = 2*height，含 AND 掩码）。

    像素内容确定性生成；32bpp 且 alpha=True 时首像素透明、其余不透明。
    """
    xor_stride = ((width * bpp + 31) // 32) * 4
    and_stride = ((width + 31) // 32) * 4

    palette = b""
    if bpp <= 8:
        entries = []
        for i in range(1 << bpp):
            entries.append(bytes(((i * 37) & 0xFF, (i * 91) & 0xFF, (i * 53) & 0xFF, 0)))
        palette = b"".join(entries)

    rows = []
    for y in range(height):  # 自底向上
        row = bytearray()
        for x in range(width):
            seed = (x * 7 + y * 13) & 0xFF
            if bpp == 32:
                a = 0 if (alpha and x == 0 and y == 0) else 255
                row += bytes((seed, (seed + 85) & 0xFF, (seed + 170) & 0xFF, a))
            elif bpp == 24:
                row += bytes((seed, (seed + 85) & 0xFF, (seed + 170) & 0xFF))
            elif bpp == 8:
                row.append(seed)
            else:
                raise ValueError("夹具仅支持 8/24/32 bpp")
        row += b"\x00" * (xor_stride - len(row))
        rows.append(bytes(row))
    xor_mask = b"".join(reversed(rows))

    and_row = bytearray(and_stride)
    if bpp != 32 or not alpha:
        and_row[0] = 0x80  # 首像素透明
    and_mask = bytes(and_row) * height

    bi_size_image = xor_stride * height + and_stride * height
    header = struct.pack(
        "<IiiHHIIiiII",
        40, width, height * 2, 1, bpp, 0, bi_size_image,
        2835, 2835, 0, 0)
    return header + palette + xor_mask + and_mask


def _png_chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def build_png_blob(width, height, rgba=True):
    """构造最小合法 PNG（RGBA8 或 RGB8，filter 0，无隔行）。"""
    color_type = 6 if rgba else 2
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            seed = (x * 5 + y * 11) & 0xFF
            raw += bytes((seed, (seed + 60) & 0xFF, (seed + 120) & 0xFF))
            if rgba:
                raw.append(0 if (x == 0 and y == 0) else 255)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw)))
            + _png_chunk(b"IEND", b""))


def build_image(spec):
    """spec: dict(size 或 (w,h), bpp, fmt='bmp'|'png', alpha=True)。"""
    size = spec.get("size", 32)
    if isinstance(size, tuple):
        width, height = size
    else:
        width = height = size
    bpp = spec.get("bpp", 32)
    fmt = spec.get("fmt", "bmp")
    alpha = spec.get("alpha", True)
    if fmt == "png":
        blob = build_png_blob(width, height, rgba=(bpp == 32))
    else:
        blob = build_bmp_blob(width, height, bpp=bpp, alpha=alpha)
    return IconImage.from_blob(blob)


def build_icon(specs):
    """按规格列表构造 IconFile。例：[dict(size=16, bpp=32), dict(size=32, fmt='png')]"""
    return IconFile([build_image(spec) for spec in specs])


# 常用规格
SPECS_SINGLE = [dict(size=32, bpp=32)]
SPECS_MULTI = [
    dict(size=16, bpp=8),
    dict(size=16, bpp=32),
    dict(size=32, bpp=8),
    dict(size=32, bpp=32),
    dict(size=48, bpp=32),
    dict(size=48, bpp=32, fmt="png"),
    dict(size=256, bpp=32, fmt="png"),
]
SPECS_ALPHA = [
    dict(size=32, bpp=32, alpha=True),
    dict(size=32, bpp=32, fmt="png"),
]
