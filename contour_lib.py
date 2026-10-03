#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contour_lib.py — 标量场等值线提取（Marching Squares，仅标准库）

核心约定
--------
1. 网格: ny 行 nx 列节点, 坐标 x = i*dx + x0, y = j*dy + y0。
   单元格 (i, j) 覆盖 [i, i+1] x [j, j+1], 共 (nx-1)*(ny-1) 个。
2. 高/低分类: 节点值 v >= level 记为"高", 否则"低"。
   该判定只依赖节点自身, 与所在单元格无关, 因此相邻格对共享边
   是否穿越的判断必然一致, 这是线段跨格首尾相接的前提。
3. 歧义格消歧（渐近判定器, asymptotic decider）:
   双线性插值 f(x,y) = a + b*x + c*y + d*x*y 的鞍点值
       s = (a*d - b*c) / (b*d - c*c)
   （a,b,c,d 为按单元格局部坐标展开的四个角点值）。
   s >= level 时按"鞍点处为高"连接, 否则按"鞍点处为低"连接。
   分母为 0（线性场, 无鞍点）时退化为"鞍点处为低"。
4. 边键（edge key）全局唯一:
   - 水平边: ("h", i, j) 表示节点 (i,j)-(i+1,j) 之间的边;
   - 垂直边: ("v", i, j) 表示节点 (i,j)-(i,j+1) 之间的边;
   - 节点退化: ("n", i, j) 表示交点恰好落在节点 (i,j) 上。
   相邻单元格引用同一条物理边时产生相同的键, 从而保证线段
   端点逐位相等、可直接拼接, 断点数恒为 0。
5. 开/闭判定: 折线含边界边键或边界节点键 => 开放等值线,
   否则为闭合等值线。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

Point = Tuple[float, float]
EdgeKey = Tuple

# 单元格边标签: 0=底 1=右 2=顶 3=左
_BOTTOM, _RIGHT, _TOP, _LEFT = 0, 1, 2, 3

# 非歧义情形线段表: case -> [(边A, 边B), ...]
# case 位编码: bit0=左下 bit1=右下 bit2=右上 bit3=左上 (1 表示高)
_TABLE = {
    0: [],
    1: [(_LEFT, _BOTTOM)],
    2: [(_BOTTOM, _RIGHT)],
    3: [(_LEFT, _RIGHT)],
    4: [(_RIGHT, _TOP)],
    6: [(_BOTTOM, _TOP)],
    7: [(_LEFT, _TOP)],
    8: [(_LEFT, _TOP)],
    9: [(_BOTTOM, _TOP)],
    11: [(_RIGHT, _TOP)],
    12: [(_LEFT, _RIGHT)],
    13: [(_BOTTOM, _RIGHT)],
    14: [(_LEFT, _BOTTOM)],
    15: [],
}

# 歧义情形（case 5 与 case 10）按鞍点高/低分别连接
_AMBIGUOUS = {
    5: {True: [(_LEFT, _TOP), (_BOTTOM, _RIGHT)],
        False: [(_LEFT, _BOTTOM), (_RIGHT, _TOP)]},
    10: {True: [(_LEFT, _BOTTOM), (_RIGHT, _TOP)],
         False: [(_LEFT, _TOP), (_BOTTOM, _RIGHT)]},
}


@dataclass
class ContourResult:
    """等值线提取结果。"""
    level: float
    segments: List[Tuple[Point, Point]]          # 原始线段（坐标对）
    closed_contours: List[List[Point]]           # 闭合等值线（首尾坐标相同）
    open_contours: List[List[Point]]             # 开放等值线（端点在边界上）
    ambiguous_cells: List[Tuple[int, int]]       # 全部歧义格 (i, j)
    saddle_high_cells: List[Tuple[int, int]]     # 其中按"鞍点为高"消歧的格
    saddle_low_cells: List[Tuple[int, int]]      # 其中按"鞍点为低"消歧的格
    breakpoints: int = 0                         # 断点数（拼接失败的端点数）
    vertex_degrees: Dict[EdgeKey, int] = field(default_factory=dict)

    @property
    def num_ambiguous(self) -> int:
        return len(self.ambiguous_cells)


def _saddle_value(a: float, b: float, c: float, d: float):
    """双线性插值鞍点值; 分母为 0（线性场）时返回 None。"""
    denom = a - b - c + d
    if denom == 0.0:
        return None
    return (a * d - b * c) / denom


def extract_contours(values, nx: int, ny: int, level: float,
                     dx: float = 1.0, dy: float = 1.0,
                     x0: float = 0.0, y0: float = 0.0) -> ContourResult:
    """从规则网格标量场提取 level 等值线。

    values: 长度 nx*ny 的序列, values[j*nx + i] 为节点 (i, j) 的值。
    """
    if nx < 2 or ny < 2:
        raise ValueError("nx 与 ny 必须 >= 2")
    if len(values) != nx * ny:
        raise ValueError("values 长度必须等于 nx*ny")

    def val(i, j):
        return values[j * nx + i]

    edge_points: Dict[EdgeKey, Point] = {}

    def edge_key_and_point(i, j, edge):
        """单元格 (i,j) 的指定边上的等值点: 返回 (键, 坐标)。"""
        if edge == _BOTTOM:
            key, (i1, j1), (i2, j2) = ("h", i, j), (i, j), (i + 1, j)
        elif edge == _TOP:
            key, (i1, j1), (i2, j2) = ("h", i, j + 1), (i, j + 1), (i + 1, j + 1)
        elif edge == _LEFT:
            key, (i1, j1), (i2, j2) = ("v", i, j), (i, j), (i, j + 1)
        else:  # _RIGHT
            key, (i1, j1), (i2, j2) = ("v", i + 1, j), (i + 1, j), (i + 1, j + 1)

        v1, v2 = val(i1, j1), val(i2, j2)
        if v1 == level:
            key = ("n", i1, j1)
        elif v2 == level:
            key = ("n", i2, j2)

        if key not in edge_points:
            if key[0] == "n":
                _, ni, nj = key
                edge_points[key] = (x0 + ni * dx, y0 + nj * dy)
            else:
                t = (level - v1) / (v2 - v1)
                if key[0] == "h":
                    pt = (x0 + (i1 + t) * dx, y0 + j1 * dy)
                else:
                    pt = (x0 + i1 * dx, y0 + (j1 + t) * dy)
                edge_points[key] = pt
        return key, edge_points[key]

    segments: List[Tuple[Point, Point]] = []
    adjacency: Dict[EdgeKey, List[int]] = {}
    ambiguous, saddle_high, saddle_low = [], [], []

    def add_segment(ka, pa, kb, pb):
        if ka == kb:  # 零长度退化段（节点恰在等值线上时可能出现）
            return
        idx = len(segments)
        segments.append((pa, pb))
        adjacency.setdefault(ka, []).append(idx)
        adjacency.setdefault(kb, []).append(idx)

    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b = val(i, j), val(i + 1, j)          # 左下, 右下
            c, d = val(i + 1, j + 1), val(i, j + 1)  # 右上, 左上
            case = ((1 if a >= level else 0)
                    | (2 if b >= level else 0)
                    | (4 if c >= level else 0)
                    | (8 if d >= level else 0))
            if case in (5, 10):
                ambiguous.append((i, j))
                s = _saddle_value(a, b, c, d)
                high = (s is not None and s >= level)
                (saddle_high if high else saddle_low).append((i, j))
                pairs = _AMBIGUOUS[case][high]
            else:
                pairs = _TABLE[case]
            for e1, e2 in pairs:
                k1, p1 = edge_key_and_point(i, j, e1)
                k2, p2 = edge_key_and_point(i, j, e2)
                add_segment(k1, p1, k2, p2)

    def is_boundary(key) -> bool:
        kind, i, j = key
        if kind == "h":
            return j == 0 or j == ny - 1
        if kind == "v":
            return i == 0 or i == nx - 1
        return i == 0 or i == nx - 1 or j == 0 or j == ny - 1

    # ---- 断点统计 ----
    # 合法端点只允许出现在域边界上; 内部顶点必须能两两配对:
    #   内部边键: 恰好属于 2 个格, 度数必须 == 2;
    #   边界边键: 只属于 1 个格, 度数 0/1 合法, >1 为断点;
    #   内部节点键: 度数必须为偶数（可有多条等值线在节点处相接）;
    #   边界节点键: 允许作为开放线端点, 度数 <= 2 合法。
    breakpoints = 0
    degrees: Dict[EdgeKey, int] = {}
    for key, seg_ids in adjacency.items():
        deg = len(seg_ids)
        degrees[key] = deg
        kind, i, j = key
        if kind == "h":
            interior = 0 < j < ny - 1
        elif kind == "v":
            interior = 0 < i < nx - 1
        else:
            interior = 0 < i < nx - 1 and 0 < j < ny - 1
        if kind in ("h", "v"):
            if (interior and deg != 2) or (not interior and deg > 1):
                breakpoints += 1
        elif interior:
            if deg % 2 != 0:
                breakpoints += 1
        elif deg > 2:
            breakpoints += 1

    # ---- 链追踪: 把线段拼成折线 ----
    used = [False] * len(segments)
    seg_endpoints: List[Tuple[EdgeKey, EdgeKey]] = [None] * len(segments)
    # 重建每个线段的端点键（按插入顺序与 adjacency 一致）
    key_of_segment: Dict[int, List[EdgeKey]] = {}
    for key, seg_ids in adjacency.items():
        for sid in seg_ids:
            key_of_segment.setdefault(sid, []).append(key)
    for sid, keys in key_of_segment.items():
        seg_endpoints[sid] = (keys[0], keys[1])

    def walk(start_sid, start_key):
        """从 start_sid 的 start_key 端出发沿链行走, 返回 (键序列, 是否闭环)。"""
        keys = [start_key]
        sid = start_sid
        at_key = start_key
        while True:
            used[sid] = True
            k1, k2 = seg_endpoints[sid]
            nxt = k2 if k1 == at_key else k1
            keys.append(nxt)
            if nxt == keys[0]:
                return keys, True
            candidates = [s for s in adjacency[nxt] if not used[s]]
            if not candidates:
                return keys, False
            sid = candidates[0]
            at_key = nxt

    def keys_to_polyline(keys):
        return [edge_points[k] for k in keys]

    closed_contours: List[List[Point]] = []
    open_contours: List[List[Point]] = []

    # 先走开放链（从度数为 1 的端点出发）, 再走剩余闭环
    starts = [(sid, k)
              for k, seg_ids in adjacency.items() if len(seg_ids) == 1
              for sid in seg_ids if not used[sid]]
    for sid, k in starts:
        if used[sid]:
            continue
        keys, is_loop = walk(sid, k)
        poly = keys_to_polyline(keys)
        (closed_contours if is_loop else open_contours).append(poly)

    for sid in range(len(segments)):
        if used[sid]:
            continue
        k1, _ = seg_endpoints[sid]
        keys, is_loop = walk(sid, k1)
        poly = keys_to_polyline(keys)
        (closed_contours if is_loop else open_contours).append(poly)

    # 按"是否触碰边界"重新归类（节点退化可能让闭环起点落在边界上）
    final_closed, final_open = [], []
    for poly in closed_contours + open_contours:
        touches_boundary = False
        # 用坐标反查键不可靠, 直接依据坐标判断是否在边界上
        for (px, py) in poly:
            on_x = abs(px - x0) < 1e-12 or abs(px - (x0 + (nx - 1) * dx)) < 1e-12
            on_y = abs(py - y0) < 1e-12 or abs(py - (y0 + (ny - 1) * dy)) < 1e-12
            if on_x or on_y:
                touches_boundary = True
                break
        (final_open if touches_boundary else final_closed).append(poly)

    return ContourResult(
        level=level,
        segments=segments,
        closed_contours=final_closed,
        open_contours=final_open,
        ambiguous_cells=ambiguous,
        saddle_high_cells=saddle_high,
        saddle_low_cells=saddle_low,
        breakpoints=breakpoints,
        vertex_degrees=degrees,
    )


def _fmt_point(p):
    return "({:.4f},{:.4f})".format(p[0], p[1])


def _demo():
    """演示: 高斯峰闭合等值线 + 平面开放等值线, 输出 SVG。"""
    import math

    nx = ny = 41
    vals = []
    for j in range(ny):
        for i in range(nx):
            x, y = i / (nx - 1), j / (ny - 1)
            g = math.exp(-((x - 0.35) ** 2 + (y - 0.6) ** 2) / 0.02)
            vals.append(g + 0.3 * x + 0.1 * y)
    level = 0.5
    res = extract_contours(vals, nx, ny, level,
                           dx=1.0 / (nx - 1), dy=1.0 / (ny - 1))
    print("level =", level)
    print("线段数:", len(res.segments))
    print("闭合等值线:", len(res.closed_contours))
    print("开放等值线:", len(res.open_contours))
    print("歧义格:", res.num_ambiguous,
          "(鞍点高: {}, 鞍点低: {})".format(len(res.saddle_high_cells),
                                            len(res.saddle_low_cells)))
    print("断点数:", res.breakpoints)

    # 输出 SVG 便于人工检查
    scale, margin = 400.0, 10.0
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="420" height="420">',
             '<rect width="420" height="420" fill="white"/>']
    for (x1, y1), (x2, y2) in res.segments:
        sx1 = margin + x1 * scale
        sy1 = margin + (1.0 - y1) * scale
        sx2 = margin + x2 * scale
        sy2 = margin + (1.0 - y2) * scale
        parts.append('<line x1="{:.2f}" y1="{:.2f}" x2="{:.2f}" y2="{:.2f}" '
                     'stroke="black" stroke-width="1"/>'.format(sx1, sy1, sx2, sy2))
    parts.append('</svg>')
    with open("contours_demo.svg", "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    print("已写出 contours_demo.svg")


if __name__ == "__main__":
    _demo()
