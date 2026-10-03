#!/usr/bin/env python3
"""roundtrip.py — ICO 封装往返对拍。

对每个样本：手工 build 写出 -> parse 读回 -> to_bytes 再次写出，
然后逐字节比对两次字节串是否一致，并打印每项的偏移/长度/色深/压缩标志
以及整文件 SHA-256，作为往返对拍数据。
"""
from __future__ import annotations

import hashlib
import os
import sys

import icoformat
from icoformat import IconImage
from icon_samples import make_bmp, make_png


def cases():
    # 1) 单尺寸 16x16
    yield "single_16", [
        IconImage(16, 16, 32, make_bmp(16, 16, 32)),
    ]
    # 2) 多尺寸重复：两个 32x32（24bpp / 32bpp 带透明）+ 16 + 48
    yield "multi_dup_sizes", [
        IconImage(16, 16, 32, make_bmp(16, 16, 32)),
        IconImage(32, 32, 24, make_bmp(32, 32, 24)),
        IconImage(32, 32, 32, make_bmp(32, 32, 32, alpha_mode="sparse")),
        IconImage(48, 48, 8, make_bmp(48, 48, 8)),
    ]
    # 3) 带透明通道：32bpp BMP + RGBA PNG
    yield "with_alpha", [
        IconImage(32, 32, 32, make_bmp(32, 32, 32, alpha_mode="sparse")),
        IconImage(256, 256, 32, make_png(256, 256, with_alpha=True)),
    ]
    # 4) 256 尺寸 + PNG 压缩 + 普通 BMP 混排
    yield "mixed_png_bmp", [
        IconImage(24, 24, 24, make_bmp(24, 24, 24)),
        IconImage(256, 256, 32, make_png(256, 256, with_alpha=True)),
    ]


def run() -> int:
    failures = 0
    for name, images in cases():
        blob1 = icoformat.build(images)
        parsed = icoformat.parse(blob1)
        blob2 = parsed.to_bytes()
        ok = blob1 == blob2
        failures += 0 if ok else 1

        print(f"=== {name} ===")
        print(f"  文件长度      : {len(blob1)} 字节")
        print(f"  目录项数量    : {len(parsed.images)}")
        print(f"  逐字节往返一致: {'PASS' if ok else 'FAIL'}")
        print(f"  sha256(写出)  : {hashlib.sha256(blob1).hexdigest()[:16]}")
        print(f"  sha256(读回写): {hashlib.sha256(blob2).hexdigest()[:16]}")
        print("  #  偏移  长度    尺寸      色深  压缩  透明")
        for im in parsed.images:
            print(f"  {im.index:<2} {im.offset:<6}{im.size:<8}"
                  f"{im.width}x{im.height:<4} {im.bit_count:<5} "
                  f"{'PNG' if im.compressed else 'BMP':<4} "
                  f"{'是' if im.has_alpha else '否'}")
        # 额外校验：读出的每段数据按偏移切回原文件必须一致
        for im in parsed.images:
            assert blob1[im.offset:im.offset + im.size] == im.data
        print()
    print(f"结果: {'全部 PASS' if failures == 0 else f'{failures} 个 FAIL'}")
    return failures


if __name__ == "__main__":
    sys.exit(run())
