"""用标准库手工合成 ICO 内嵌图像样本（BMP-DIB / PNG），供对拍与测试使用。

不调用任何图像库；PNG 仅用 zlib 做 IDAT 压缩。
"""
from __future__ import annotations

import struct
import zlib


def make_bmp(width: int, height: int, bit_count: int = 32,
             *, alpha_mode: str = "opaque") -> bytes:
    """生成 ICO 内嵌的自下而上 DIB（BITMAPINFOHEADER + XOR 掩码 + AND 掩码）。

    alpha_mode:
      - "opaque"：32bpp 时 alpha 全 255；
      - "sparse"：每隔一个像素 alpha 为 0（真透明通道）；
    低色深（1/4/8/24）像素数据填零，AND 掩码按行 4 字节对齐。
    """
    if bit_count <= 8:
        planes_row = ((width * bit_count + 31) // 32) * 4
        xor = b"\x00" * (planes_row * height)
        palette = b"\x00" * (min(1 << bit_count, 256) * 4)
    elif bit_count == 24:
        planes_row = ((width * 3 + 3) // 4) * 4
        xor = b"\x00" * (planes_row * height)
        palette = b""
    elif bit_count == 32:
        xor = bytearray(width * height * 4)
        for y in range(height):
            for x in range(width):
                k = (y * width + x) * 4
                xor[k:k + 3] = bytes(((x * 7) & 255, (y * 11) & 255, 128))
                if alpha_mode == "sparse" and (x + y) % 2 == 0:
                    xor[k + 3] = 0
                else:
                    xor[k + 3] = 255
        xor = bytes(xor)
        palette = b""
    else:
        raise ValueError(f"不支持的色深 {bit_count}")

    and_row = ((width + 31) // 32) * 4
    and_mask = b"\x00" * (and_row * height)  # 全 0 = 不透明

    dib = struct.pack(
        "<IiiHHIIiiII",
        40, width, height * 2, 1, bit_count,
        0,                         # BI_RGB
        len(xor) + len(and_mask),  # 图像长度（可留 0，给全更规范）
        0, 0, 0, 0,
    )
    return dib + palette + xor + and_mask


def make_png(width: int, height: int, *, with_alpha: bool = True) -> bytes:
    """生成最小 RGBA 或 RGB PNG（IHDR / IDAT / IEND，每像素一行滤波字节 0）。"""
    color_type = 6 if with_alpha else 2
    channels = 4 if with_alpha else 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type none
        for x in range(width):
            raw.extend(((x * 7) & 255, (y * 11) & 255, 128,
                        0 if (with_alpha and (x + y) % 2 == 0) else 255))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            chunk(b"IEND", b""))
