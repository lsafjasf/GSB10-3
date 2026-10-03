"""histeq.py —— 纯标准库实现的直方图均衡 / 分块局部直方图增强。

只依赖 Python 3 标准库。

支持两种图像表示（像素值均为 0~255 的整数）：
  - 灰度图：list[list[int]]，shape = (H, W)
  - 彩色图：list[list[list[int]]]，shape = (H, W, C)（如 RGB）

公共接口：
  equalize_hist(img, clip_limit=None)
      全局直方图均衡（clip_limit 非 None 时做对比度受限的裁剪/重分配）。
  local_equalize(img, tile=(8, 8), clip_limit=None, interpolate=True)
      分块局部增强。每个分块独立计算均衡 LUT，块间用双线性插值
      （OpenCV CLAHE 同款的分区加权策略）消除方格接缝；interpolate=False
      时退化为“每块直接套自己的 LUT”，用于对比验证接缝问题。
  image_stats / histogram / distribution   —— 分布统计工具。
"""

from bisect import bisect_right

LEVELS = 256


# --------------------------------------------------------------------------
# 图像表示工具
# --------------------------------------------------------------------------
def _is_color(img):
    return bool(img) and isinstance(img[0][0], list)


def shape(img):
    """返回 (H, W) 或 (H, W, C)。"""
    h = len(img)
    w = len(img[0]) if h else 0
    if _is_color(img):
        return h, w, len(img[0][0])
    return h, w


def split_channels(img):
    """彩色图 -> [灰度图, ...]；灰度图 -> [灰度图]。"""
    if not _is_color(img):
        return [img]
    h, w, c = shape(img)
    return [[[img[y][x][k] for x in range(w)] for y in range(h)]
            for k in range(c)]


def merge_channels(chans):
    h, w = len(chans[0]), len(chans[0][0])
    return [[[chans[k][y][x] for k in range(len(chans))]
             for x in range(w)] for y in range(h)]


# --------------------------------------------------------------------------
# 直方图 / LUT
# --------------------------------------------------------------------------
def histogram(chan):
    """单通道直方图，长度 256。"""
    hist = [0] * LEVELS
    for row in chan:
        for v in row:
            hist[v] += 1
    return hist


def _clip_redistribute(hist, total, clip_limit):
    """对比度受限：裁剪直方图并把超出部分均匀重分配（OpenCV 同款单遍算法）。

    裁剪后把超出总量先均摊到所有 bin，余数按固定步长逐 bin 补 1，
    保证重分配前后像素总数严格守恒。
    """
    hist = hist[:]
    excess = 0
    for i in range(LEVELS):
        if hist[i] > clip_limit:
            excess += hist[i] - clip_limit
            hist[i] = clip_limit
    batch, residual = divmod(excess, LEVELS)
    for i in range(LEVELS):
        hist[i] += batch
    if residual:
        step = max(1, LEVELS // residual)
        i = 0
        while residual > 0:
            hist[i] += 1
            residual -= 1
            i = (i + step) % LEVELS
    return hist


def make_lut(hist, total, clip_limit=None):
    """由直方图生成均衡 LUT（经典 CDF 映射，输出铺满 0~255）。

    单值图（直方图只有一个非零 bin）退化为恒等映射，保持原值不变，
    避免全黑/全白/单色图被错误拉伸。
    """
    if clip_limit is not None:
        hist = _clip_redistribute(hist, total, clip_limit)
    cdf = []
    acc = 0
    for c in hist:
        acc += c
        cdf.append(acc)
    cdf_min = next((c for c in cdf if c > 0), 0)
    denom = total - cdf_min
    if denom <= 0:  # 单值图
        return list(range(LEVELS))
    lut = [0] * LEVELS
    for v in range(LEVELS):
        if cdf[v] > cdf_min:
            lut[v] = min(LEVELS - 1,
                         round((cdf[v] - cdf_min) * (LEVELS - 1) / denom))
    return lut


def _tile_breaks(n, tiles):
    """把长度 n 尽量均匀切成 tiles 段，返回边界位置（tiles+1 个）。"""
    base, rem = divmod(n, tiles)
    breaks = [0]
    for i in range(tiles):
        breaks.append(breaks[-1] + base + (1 if i < rem else 0))
    return breaks


def _grid_luts(chan, ty, tx, clip_limit):
    """计算每个分块的均衡 LUT 及其分块边界。"""
    h, w = len(chan), len(chan[0])
    by, bx = _tile_breaks(h, ty), _tile_breaks(w, tx)
    luts = [[None] * tx for _ in range(ty)]
    for i in range(ty):
        for j in range(tx):
            hist = [0] * LEVELS
            for y in range(by[i], by[i + 1]):
                row = chan[y]
                for x in range(bx[j], bx[j + 1]):
                    hist[row[x]] += 1
            total = (by[i + 1] - by[i]) * (bx[j + 1] - bx[j])
            luts[i][j] = make_lut(hist, total, clip_limit)
    return luts, by, bx


# --------------------------------------------------------------------------
# 全局均衡
# --------------------------------------------------------------------------
def equalize_channel(chan, clip_limit=None):
    h, w = len(chan), len(chan[0])
    lut = make_lut(histogram(chan), h * w, clip_limit)
    return [[lut[v] for v in row] for row in chan]


def equalize_hist(img, clip_limit=None):
    """全局直方图均衡。彩色图逐通道处理，通道数与尺寸保持不变。"""
    chans = split_channels(img)
    out = [equalize_channel(c, clip_limit) for c in chans]
    return merge_channels(out) if len(out) > 1 else out[0]


# --------------------------------------------------------------------------
# 分块局部增强（块间双线性插值）
# --------------------------------------------------------------------------
def local_equalize_channel(chan, tile=(8, 8), clip_limit=None,
                           interpolate=True):
    h, w = len(chan), len(chan[0])
    if isinstance(tile, int):
        tile = (tile, tile)
    ty = max(1, min(tile[0], h))
    tx = max(1, min(tile[1], w))
    luts, by, bx = _grid_luts(chan, ty, tx, clip_limit)

    cy = [(by[i] + by[i + 1]) // 2 for i in range(ty)]
    cx = [(bx[j] + bx[j + 1]) // 2 for j in range(tx)]

    out = [[0] * w for _ in range(h)]
    for y in range(h):
        # 像素所在分块（blocky 模式用）
        bi = min(ty - 1, bisect_right(by, y) - 1)
        # 插值权重：相邻两行分块中心 LUT 的混合权重
        i1 = min(ty - 1, bisect_right(cy, y))
        i0 = i1 - 1
        if i0 < 0:
            i0, wy0, wy1 = 0, 256, 0
        elif i1 >= ty or cy[i1] == cy[i0]:
            i1, wy0, wy1 = i0, 256, 0
        else:
            wy0 = (cy[i1] - y) * 256 // (cy[i1] - cy[i0])
            wy1 = 256 - wy0
        for x in range(w):
            bj = min(tx - 1, bisect_right(bx, x) - 1)
            v = chan[y][x]
            if not interpolate:
                out[y][x] = luts[bi][bj][v]
                continue
            j1 = min(tx - 1, bisect_right(cx, x))
            j0 = j1 - 1
            if j0 < 0:
                j0, wx0, wx1 = 0, 256, 0
            elif j1 >= tx or cx[j1] == cx[j0]:
                j1, wx0, wx1 = j0, 256, 0
            else:
                wx0 = (cx[j1] - x) * 256 // (cx[j1] - cx[j0])
                wx1 = 256 - wx0

            v00 = luts[i0][j0][v]
            v01 = luts[i0][j1][v]
            v10 = luts[i1][j0][v]
            v11 = luts[i1][j1][v]
            top = (v00 * wx0 + v01 * wx1 + 128) // 256
            bot = (v10 * wx0 + v11 * wx1 + 128) // 256
            val = (top * wy0 + bot * wy1 + 128) // 256
            out[y][x] = val if 0 <= val <= 255 else (0 if val < 0 else 255)
    return out


def local_equalize(img, tile=(8, 8), clip_limit=None, interpolate=True):
    """分块局部直方图增强，块间双线性插值消除接缝。

    tile: (纵向块数, 横向块数)；块大小随图像尺寸自动尽量均分。
    clip_limit: None 或正整数（每 bin 计数上限），实现对比度受限增强。
    interpolate: False 时不做块间插值，用于复现/检查方格接缝。
    """
    chans = split_channels(img)
    out = [local_equalize_channel(c, tile, clip_limit, interpolate)
           for c in chans]
    return merge_channels(out) if len(out) > 1 else out[0]


# --------------------------------------------------------------------------
# 分布统计
# --------------------------------------------------------------------------
def distribution(chan, bins=16):
    """把 0~255 分成 bins 段，返回各段像素占比（百分比，保留 2 位小数）。"""
    hist = histogram(chan)
    total = len(chan) * len(chan[0])
    edges = [round(k * 255 / bins) for k in range(bins + 1)]
    dist = []
    for k in range(bins):
        lo, hi = edges[k], edges[k + 1]
        count = sum(hist[lo:hi + (1 if k == bins - 1 else 0)])
        dist.append(round(100.0 * count / total, 2) if total else 0.0)
    return dist, edges


def channel_stats(chan):
    flat = [v for row in chan for v in row]
    n = len(flat)
    if n == 0:
        return {}
    mean = sum(flat) / n
    var = sum((v - mean) ** 2 for v in flat) / n
    s = sorted(flat)
    return {
        "min": s[0],
        "max": s[-1],
        "mean": round(mean, 2),
        "std": round(var ** 0.5, 2),
        "median": s[n // 2],
        "distinct": len(set(flat)),
    }


def image_stats(img):
    """每个通道返回一组统计量。"""
    chans = split_channels(img)
    return [channel_stats(c) for c in chans]
