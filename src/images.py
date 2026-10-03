"""生成各类型的测试图像（raw 字节 + 尺寸 + bpp），用于自测与体积对比。"""

import math
import random


def make_solid(width, height, bpp, value=128):
    """大面积同色：整幅图同一像素值。"""
    return bytes([value]) * (width * height * bpp), width, height, bpp


def make_hgradient(width, height, bpp):
    """水平渐变：每行内像素值随 x 递增，行间一致。"""
    raw = bytearray()
    for _ in range(height):
        for x in range(width):
            v = (x * 255) // max(1, width - 1)
            raw.extend([v] * bpp)
    return bytes(raw), width, height, bpp


def make_vgradient(width, height, bpp):
    """垂直渐变：像素值随 y 递增，每行内部相同。"""
    raw = bytearray()
    for y in range(height):
        v = (y * 255) // max(1, height - 1)
        raw.extend([v] * (width * bpp))
    return bytes(raw), width, height, bpp


def make_noise(width, height, bpp, seed=42):
    """随机噪声：均匀分布的随机字节。"""
    rng = random.Random(seed)
    raw = bytes(rng.randrange(256) for _ in range(width * height * bpp))
    return raw, width, height, bpp


def make_photo(width, height, bpp):
    """类照片：平滑的正弦曲面 + 轻微噪声。"""
    rng = random.Random(7)
    raw = bytearray()
    for y in range(height):
        for x in range(width):
            base = 128 + int(90 * math.sin(x / 9.0) * math.cos(y / 7.0))
            for c in range(bpp):
                v = base + c * 13 + rng.randrange(-3, 4)
                raw.append(max(0, min(255, v)))
    return bytes(raw), width, height, bpp


def make_text(width, height, bpp):
    """类文本/线条图：白底 + 周期性黑色横条与竖条。"""
    raw = bytearray()
    for y in range(height):
        for x in range(width):
            v = 0 if (y % 16 < 2 or x % 32 < 2) else 255
            raw.extend([v] * bpp)
    return bytes(raw), width, height, bpp


def make_blocks(width, height, bpp, block=16, seed=1):
    """棋盘色块：block 大小的方块，颜色伪随机。"""
    rng = random.Random(seed)
    palette = [bytes(rng.randrange(256) for _ in range(bpp)) for _ in range(8)]
    raw = bytearray()
    for y in range(height):
        for x in range(width):
            idx = ((x // block) + (y // block)) % len(palette)
            raw.extend(palette[idx])
    return bytes(raw), width, height, bpp


def all_types(width=64, height=48, bpp=3):
    """返回 {名称: (raw, w, h, bpp)}，覆盖不同图像类型。"""
    return {
        "solid(大面积同色)": make_solid(width, height, bpp),
        "hgradient(水平渐变)": make_hgradient(width, height, bpp),
        "vgradient(垂直渐变)": make_vgradient(width, height, bpp),
        "noise(随机噪声)": make_noise(width, height, bpp),
        "photo(类照片)": make_photo(width, height, bpp),
        "text(线条文本)": make_text(width, height, bpp),
        "blocks(棋盘色块)": make_blocks(width, height, bpp),
    }
