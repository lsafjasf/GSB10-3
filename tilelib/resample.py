"""重采样：固定使用面积平均（area average），不用最近邻。

栅格表示：list[list[float]]，src[y][x]，像素 (x, y) 覆盖 [x, x+1) x [y, y+1)。
窗口 window = (x0, y0, x1, y1) 为源像素坐标（可为浮点），
输出 out_h x out_w，每个输出像素的值 = 其在源窗口内足迹的面积加权平均。

提供两个面积平均实现用于数值对拍：
  - area_average_reference：直观的二维暴力逐像素求和（作为参考真值）；
  - area_average：可分离两遍实现（先水平后垂直），数学上与参考实现等价，
    浮点求和顺序不同，差异应在 1e-12 量级。
另提供 nearest_neighbor 仅用于对照，证明二者不可互换。
"""

from __future__ import annotations

import math


def _axis_weights(lo: float, hi: float, n_out: int):
    """一维面积权重：输出单元 i 覆盖 [lo + i*d, lo + (i+1)*d)，
    返回每个输出单元的 [(源下标, 权重), ...]，权重和为 1。"""
    d = (hi - lo) / n_out
    result = []
    for i in range(n_out):
        a = lo + i * d
        b = a + d
        weights = []
        k0 = math.floor(a)
        k1 = math.ceil(b)
        for k in range(k0, k1):
            overlap = min(b, k + 1) - max(a, k)
            if overlap > 1e-15:
                weights.append((k, overlap / d))
        result.append(weights)
    return result


def _check_window(src, window):
    h = len(src)
    w = len(src[0]) if h else 0
    x0, y0, x1, y1 = window
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError(f"window {window} outside raster {w}x{h}")


def area_average_reference(src, window, out_w: int, out_h: int):
    """参考实现：逐输出像素对足迹内所有源像素做二维面积加权求和。"""
    _check_window(src, window)
    x0, y0, x1, y1 = window
    dx = (x1 - x0) / out_w
    dy = (y1 - y0) / out_h
    cell_area = dx * dy
    out = []
    for j in range(out_h):
        ay0 = y0 + j * dy
        ay1 = ay0 + dy
        row = []
        for i in range(out_w):
            ax0 = x0 + i * dx
            ax1 = ax0 + dx
            acc = 0.0
            for sy in range(math.floor(ay0), math.ceil(ay1)):
                oy = min(ay1, sy + 1) - max(ay0, sy)
                if oy <= 0:
                    continue
                for sx in range(math.floor(ax0), math.ceil(ax1)):
                    ox = min(ax1, sx + 1) - max(ax0, sx)
                    if ox <= 0:
                        continue
                    acc += ox * oy * src[sy][sx]
            row.append(acc / cell_area)
        out.append(row)
    return out


def area_average(src, window, out_w: int, out_h: int):
    """可分离两遍面积平均：先水平方向加权，再垂直方向加权。

    与 area_average_reference 数学等价（面积 = 两个一维重叠长度之积），
    复杂度从 O(out * 窗口面积) 降到 O(out * 窗口边长)。
    """
    _check_window(src, window)
    x0, y0, x1, y1 = window
    wx = _axis_weights(x0, x1, out_w)
    wy = _axis_weights(y0, y1, out_h)
    rows_needed = sorted({sy for weights in wy for sy, _ in weights})
    hpass = {}
    for sy in rows_needed:
        src_row = src[sy]
        hpass[sy] = [
            sum(w * src_row[sx] for sx, w in wx[i]) for i in range(out_w)
        ]
    return [
        [sum(w * hpass[sy][i] for sy, w in wy[j]) for i in range(out_w)]
        for j in range(out_h)
    ]


def nearest_neighbor(src, window, out_w: int, out_h: int):
    """最近邻采样，仅用于对照实验，不用于生产路径。"""
    _check_window(src, window)
    x0, y0, x1, y1 = window
    dx = (x1 - x0) / out_w
    dy = (y1 - y0) / out_h
    h = len(src)
    w = len(src[0])
    out = []
    for j in range(out_h):
        sy = min(int(y0 + (j + 0.5) * dy), h - 1)
        row = []
        for i in range(out_w):
            sx = min(int(x0 + (i + 0.5) * dx), w - 1)
            row.append(src[sy][sx])
        out.append(row)
    return out


def max_abs_diff(a, b) -> float:
    """两个等形栅格的最大绝对差。"""
    return max(
        abs(a[j][i] - b[j][i])
        for j in range(len(a))
        for i in range(len(a[0]))
    )
