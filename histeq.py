# -*- coding: utf-8 -*-
"""histeq.py — 直方图均衡与分块局部增强（纯 Python 3 标准库，无第三方依赖）

图像表示：
  - 灰度图：list[list[int]]，img[y][x]，取值 0..255
  - 彩色图：list[list[tuple/list]]，img[y][x] 为长度为 C 的序列，逐通道处理

提供：
  equalize_global(img)                全局直方图均衡
  equalize_local(img, tiles, clip)    分块局部增强（CLAHE，块间双线性插值）
  histogram / stats / local_contrast  分布与对比度统计
  read_pgm / write_pgm                简易 PGM(P2/P5) 读写，便于接真实影像
"""

import math
import struct

GRAY = 256


# ---------------------------------------------------------------- 基础工具

def _is_color(img):
    return isinstance(img[0][0], (list, tuple))


def image_shape(img):
    """返回 (高, 宽, 通道数)。"""
    h = len(img)
    w = len(img[0])
    c = len(img[0][0]) if _is_color(img) else 1
    return h, w, c


def histogram(plane, bins=GRAY):
    hist = [0] * bins
    for row in plane:
        for v in row:
            hist[int(v)] += 1
    return hist


def stats(plane):
    """返回 dict：n, min, max, mean, std。"""
    n = 0
    s = 0.0
    s2 = 0.0
    lo, hi = 255, 0
    for row in plane:
        for v in row:
            n += 1
            s += v
            s2 += v * v
            if v < lo:
                lo = v
            if v > hi:
                hi = v
    mean = s / n if n else 0.0
    var = max(0.0, s2 / n - mean * mean) if n else 0.0
    return {"n": n, "min": lo, "max": hi, "mean": mean, "std": math.sqrt(var)}


def local_contrast(plane, block=8):
    """局部对比度指标：各 block 内标准差的平均值（衡量局部细节强弱）。"""
    h, w = len(plane), len(plane[0])
    total, cnt = 0.0, 0
    for y0 in range(0, h, block):
        for x0 in range(0, w, block):
            vals = [plane[y][x]
                    for y in range(y0, min(y0 + block, h))
                    for x in range(x0, min(x0 + block, w))]
            m = sum(vals) / len(vals)
            total += math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))
            cnt += 1
    return total / cnt if cnt else 0.0


def _to_planes(img):
    if _is_color(img):
        h, w, c = image_shape(img)
        return [[[img[y][x][ch] for x in range(w)] for y in range(h)]
                for ch in range(c)], True
    return [img], False


def _from_planes(planes, color):
    if not color:
        return planes[0]
    h, w = len(planes[0]), len(planes[0][0])
    return [[tuple(planes[ch][y][x] for ch in range(len(planes)))
             for x in range(w)] for y in range(h)]


# ---------------------------------------------------------------- 全局均衡

def _cdf_mapping(hist, total):
    """经典全局直方图均衡映射（cdf_min 归一化）。单值图像返回恒等映射。"""
    cdf = []
    acc = 0
    for cnt in hist:
        acc += cnt
        cdf.append(acc)
    cdf_min = next((c for c in cdf if c > 0), 0)
    if total == 0 or cdf_min >= total:      # 空图或单值图：保持不变
        return list(range(GRAY))
    return [min(GRAY - 1, max(0, round((c - cdf_min) / (total - cdf_min)
                                       * (GRAY - 1)))) for c in cdf]


def _equalize_global_plane(plane):
    mapping = _cdf_mapping(histogram(plane), len(plane) * len(plane[0]))
    return [[mapping[v] for v in row] for row in plane]


def equalize_global(img):
    """全局直方图均衡。彩色图逐通道处理。尺寸与通道数不变。"""
    planes, color = _to_planes(img)
    return _from_planes([_equalize_global_plane(p) for p in planes], color)


# ---------------------------------------------------------------- 分块局部增强（CLAHE）

def _clipped_cdf_mapping(hist, total, clip_limit):
    """单块的对比度受限 CDF 映射：直方图裁剪 + 超出部分均匀再分配。"""
    nonzero = sum(1 for c in hist if c > 0)
    if total == 0 or nonzero <= 1:          # 块内单值：恒等，避免放大噪声
        return list(range(GRAY))
    clipped = [min(c, clip_limit) for c in hist]
    excess = total - sum(clipped)
    per_bin, rem = divmod(excess, GRAY)
    redist = [c + per_bin for c in clipped]
    for i in range(rem):
        redist[i] += 1
    mapping = []
    acc = 0
    for c in redist:
        acc += c
        mapping.append(min(GRAY - 1, round(acc / total * (GRAY - 1))))
    return mapping


def _tile_bounds(length, n_tiles, i):
    return round(i * length / n_tiles), round((i + 1) * length / n_tiles)


def _equalize_local_plane(plane, tiles=8, clip_limit=None, interpolate=True):
    h, w = len(plane), len(plane[0])
    nt_y = max(1, min(tiles, h))
    nt_x = max(1, min(tiles, w))

    # 1) 每块计算裁剪后的 CDF 映射表
    maps = []
    for i in range(nt_y):
        y0, y1 = _tile_bounds(h, nt_y, i)
        row_maps = []
        for j in range(nt_x):
            x0, x1 = _tile_bounds(w, nt_x, j)
            hist = [0] * GRAY
            for y in range(y0, y1):
                row = plane[y]
                for x in range(x0, x1):
                    hist[row[x]] += 1
            n = (y1 - y0) * (x1 - x0)
            limit = clip_limit if clip_limit is not None \
                else max(1, int(3 * n / GRAY))   # 默认裁剪阈值 ≈ 3×均值
            row_maps.append(_clipped_cdf_mapping(hist, n, limit))
        maps.append(row_maps)

    # 2) 逐像素：取相邻 4 块的映射做双线性插值（边缘自动退化为最近块）
    def axis_param(pos, length, n_tiles):
        f = (pos + 0.5) * n_tiles / length - 0.5   # 块中心位于整数坐标
        f = min(max(f, 0.0), n_tiles - 1.0)
        i0 = int(math.floor(f))
        i1 = min(i0 + 1, n_tiles - 1)
        return i0, i1, f - i0

    out = []
    for y in range(h):
        iy0, iy1, wy = axis_param(y, h, nt_y)
        row_out = []
        for x in range(w):
            ix0, ix1, wx = axis_param(x, w, nt_x)
            v = plane[y][x]
            if not interpolate:
                # 对照组：不做插值，直接用最近块映射（会产生方格接缝）
                ti = min(int((y + 0.5) * nt_y / h), nt_y - 1)
                tj = min(int((x + 0.5) * nt_x / w), nt_x - 1)
                row_out.append(maps[ti][tj][v])
                continue
            m00 = maps[iy0][ix0][v]
            m01 = maps[iy0][ix1][v]
            m10 = maps[iy1][ix0][v]
            m11 = maps[iy1][ix1][v]
            top = m00 + (m01 - m00) * wx
            bot = m10 + (m11 - m10) * wx
            row_out.append(min(GRAY - 1, max(0, round(top + (bot - top) * wy))))
        out.append(row_out)
    return out


def equalize_local(img, tiles=8, clip_limit=None, interpolate=True):
    """分块局部增强（CLAHE）。

    tiles       每轴分块数（自动 clamp 到图像尺寸内）
    clip_limit  直方图裁剪阈值；None 时取 3×块内均值
    interpolate True 时块间双线性插值（消除方格接缝）；False 为对照组
    彩色图逐通道处理。尺寸与通道数不变。
    """
    planes, color = _to_planes(img)
    return _from_planes([_equalize_local_plane(p, tiles, clip_limit, interpolate)
                         for p in planes], color)


# ---------------------------------------------------------------- PGM 读写（可选，便于接真实影像）

def read_pgm(path):
    with open(path, "rb") as f:
        data = f.read()
    tokens, i = [], 0
    while len(tokens) < 4:
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while i < len(data) and data[i:i + 1] != b"\n":
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    magic = tokens[0]
    w, h, maxv = int(tokens[1]), int(tokens[2]), int(tokens[3])
    i += 1
    if magic == b"P5":
        if maxv < 256:
            raw = data[i:i + w * h]
            img = [[raw[y * w + x] for x in range(w)] for y in range(h)]
        else:
            raw = struct.unpack(">%dH" % (w * h), data[i:i + 2 * w * h])
            img = [[raw[y * w + x] for x in range(w)] for y in range(h)]
    elif magic == b"P2":
        vals = [int(t) for t in data[i:].split()]
        img = [[vals[y * w + x] for x in range(w)] for y in range(h)]
    else:
        raise ValueError("仅支持 P2/P5 PGM")
    if maxv != 255:
        img = [[round(v * 255 / maxv) for v in row] for row in img]
    return img


def write_pgm(plane, path):
    h, w = len(plane), len(plane[0])
    with open(path, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (w, h))
        f.write(bytes(v for row in plane for v in row))
