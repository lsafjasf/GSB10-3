"""凸包与旋转卡壳（仅依赖 Python 3 标准库）。

功能：
- convex_hull            : Andrew 单调链凸包，支持共线点保留/剔除、重合点去重
- hull_diameter          : 旋转卡壳求最远点对（点集直径）
- min_enclosing_rectangle: 旋转卡壳求最小面积包围矩形
- signed_area2 / is_ccw / validate_ccw_polygon: 面积与朝向工具

坐标可以是 int 或 float；int 输入时叉积/点积全程为整数，无精度误差。
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float]
Segment = Tuple[Point, Point]


# ---------- 基础向量运算 ----------

def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def cross2(u: Point, v: Point) -> float:
    """向量叉积 u × v。>0 表示 v 在 u 逆时针（左）侧。"""
    return u[0] * v[1] - u[1] * v[0]


def cross(o: Point, a: Point, b: Point) -> float:
    """(a-o) × (b-o)。"""
    return cross2(sub(a, o), sub(b, o))


def dot2(u: Point, v: Point) -> float:
    return u[0] * v[0] + u[1] * v[1]


def dist2(a: Point, b: Point) -> float:
    d = sub(a, b)
    return dot2(d, d)


# ---------- 面积与朝向 ----------

def signed_area2(poly: Sequence[Point]) -> float:
    """有符号面积的 2 倍（鞋带公式）。CCW 为正，CW 为负。"""
    s = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        s += a[0] * b[1] - a[1] * b[0]
    return s


def is_ccw(poly: Sequence[Point]) -> bool:
    return signed_area2(poly) > 0


def validate_ccw_polygon(poly: Sequence[Point]) -> None:
    """断言：多边形非退化、严格 CCW、且为凸多边形。"""
    n = len(poly)
    assert n >= 3, f"多边形至少需要 3 个顶点，实际 {n}"
    assert signed_area2(poly) > 0, "多边形必须为逆时针（CCW）朝向"
    for i in range(n):
        c = cross(poly[i], poly[(i + 1) % n], poly[(i + 2) % n])
        assert c >= 0, f"多边形非凸或非 CCW：顶点 {i} 处叉积 {c} < 0"


# ---------- 凸包 ----------

def convex_hull(points: Sequence[Point], keep_collinear: bool = False) -> List[Point]:
    """Andrew 单调链凸包。

    重合点策略：先排序去重，重合点只保留一个。
    共线点策略：
      keep_collinear=False（默认）：只保留真正的拐角顶点（最小顶点表示）；
      keep_collinear=True        ：落在凸包边界上的共线点全部保留，按外轮廓
                                   顺序排列；全部共线时从一端排到另一端。
    退化情形：
      全部重合 -> 1 个点；全部共线 -> 端点 2 个（keep 时为全部互异点）。
    返回顺序为逆时针（CCW），退化情形除外（不足 3 点无朝向可言）。
    """
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    # 检测全部共线：找第一个不与 (p0,p1) 共线的点
    p0, p1 = pts[0], pts[1]
    collinear_all = True
    for q in pts[2:]:
        if cross(p0, p1, q) != 0:
            collinear_all = False
            break
    if collinear_all:
        return pts if keep_collinear else [pts[0], pts[-1]]

    # cross < 0 弹出 => 保留共线点；cross <= 0 弹出 => 剔除共线点
    def build(seq: Sequence[Point]) -> List[Point]:
        stack: List[Point] = []
        for p in seq:
            while len(stack) >= 2:
                c = cross(stack[-2], stack[-1], p)
                if c < 0 or (not keep_collinear and c == 0):
                    stack.pop()
                else:
                    break
            stack.append(p)
        return stack

    lower = build(pts)
    upper = build(reversed(pts))
    return lower[:-1] + upper[:-1]


# ---------- 旋转卡壳：直径（最远点对） ----------

def hull_diameter(hull: Sequence[Point]) -> Tuple[float, Segment]:
    """旋转卡壳求凸多边形的直径（最远点对距离）及该点对。hull 须为 CCW。"""
    h = len(hull)
    if h == 1:
        return 0.0, (hull[0], hull[0])
    if h == 2:
        return math.sqrt(dist2(hull[0], hull[1])), (hull[0], hull[1])
    validate_ccw_polygon(hull)

    # 初始化为首条边的对踵点（距离最大顶点），之后单调推进
    a0, b0 = hull[0], hull[1]
    e0 = sub(b0, a0)
    j = max(range(h), key=lambda idx: cross2(e0, sub(hull[idx], a0)))
    best_d2 = -1.0
    best_pair: Segment = (hull[0], hull[0])

    for i in range(h):
        a, b = hull[i], hull[(i + 1) % h]
        e = sub(b, a)
        # 推进到对该边距离（叉积）最大的顶点
        while True:
            cur = cross2(e, sub(hull[j], a))
            nxt = cross2(e, sub(hull[(j + 1) % h], a))
            if nxt > cur:
                j = (j + 1) % h
            else:
                break
        for p in (a, b):
            d2 = dist2(hull[j], p)
            if d2 > best_d2:
                best_d2, best_pair = d2, (p, hull[j])

    return math.sqrt(best_d2), best_pair


# ---------- 旋转卡壳：最小面积包围矩形 ----------

def min_enclosing_rectangle(hull: Sequence[Point]):
    """旋转卡壳求最小面积外接矩形。

    依据：凸多边形最小面积外接矩形必有一条边与某条凸包边共线。
    对每条边维护 3 个单调指针：
      j : 垂直方向最远点（最大叉积，矩形的高）
      k : 沿边方向投影最大点
      l : 沿边方向投影最小点
    返回 (面积, 四个角点 CCW)。退化（点/线段）时面积为 0。
    """
    h = len(hull)
    if h == 1:
        p = hull[0]
        return 0.0, [p, p, p, p]
    if h == 2:
        a, b = hull
        return 0.0, [a, a, b, b]
    validate_ccw_polygon(hull)

    # 按首条边扫描初始化三个指针：高（最大叉积）、投影最大、投影最小
    a0, b0 = hull[0], hull[1]
    e0 = sub(b0, a0)
    j = max(range(h), key=lambda idx: cross2(e0, sub(hull[idx], a0)))
    k = max(range(h), key=lambda idx: dot2(e0, hull[idx]))
    l = min(range(h), key=lambda idx: dot2(e0, hull[idx]))
    best_area = math.inf
    best = None

    for i in range(h):
        a, b = hull[i], hull[(i + 1) % h]
        e = sub(b, a)
        length2 = dot2(e, e)

        def height(idx: int) -> float:
            return cross2(e, sub(hull[idx], a))

        def proj(idx: int) -> float:
            return dot2(e, hull[idx])

        # 凸多边形上高度/投影沿边界单峰，指针严格单调推进，总复杂度 O(h)
        while height((j + 1) % h) > height(j):
            j = (j + 1) % h
        while proj((k + 1) % h) > proj(k):
            k = (k + 1) % h
        while proj((l + 1) % h) < proj(l):
            l = (l + 1) % h

        # 高 = height(j)/|e|，宽 = (proj(k)-proj(l))/|e|
        area = height(j) * (proj(k) - proj(l)) / length2
        if area < best_area:
            best_area = area
            best = (i, j, k, l)

    i, j, k, l = best
    a, b = hull[i], hull[(i + 1) % h]
    e = sub(b, a)
    length = math.sqrt(dot2(e, e))
    u = (e[0] / length, e[1] / length)       # 沿边单位向量
    nrm = (-u[1], u[0])                       # 左侧单位法向量
    d0 = dot2(hull[l], u)                     # 最小沿边投影
    d1 = dot2(hull[k], u)                     # 最大沿边投影
    base = dot2(a, nrm)                       # 底边法向位置（多边形整体在左侧）
    top = base + cross2(e, sub(hull[j], a)) / length

    def corner(d: float, t: float) -> Point:
        return (d * u[0] + t * nrm[0], d * u[1] + t * nrm[1])

    corners = [corner(d0, base), corner(d1, base),
               corner(d1, top), corner(d0, top)]
    return best_area, corners
