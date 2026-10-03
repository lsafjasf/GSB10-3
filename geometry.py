"""
geometry.py — 凸包 + 旋转卡壳（仅标准库）

点用 (x, y) 元组表示，坐标为 float 或 int。

约定：
- 凸包顶点按逆时针（CCW）排列，不含重复点，首尾不闭合（末点不重复首点）。
- 共线点保留策略（keep_collinear 参数）：
    keep_collinear=False（默认）：只保留每条共线边的两个端点，
        得到顶点数最少的凸包。旋转卡壳应使用此策略。
    keep_collinear=True：位于凸包边上的共线点全部保留，
        便于需要"边界上所有点"的场景。
- 重合点：先按坐标去重后再求凸包。
- 唯一点数 < 3 时返回去重排序后的点本身（1 或 2 个点），面积为 0。
"""

import math

EPS = 1e-12


def cross(o, a, b):
    """向量 OA 与 OB 的叉积；>0 表示 OB 在 OA 左侧（逆时针）。"""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def dist2(a, b):
    """两点距离的平方。"""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def convex_hull(points, keep_collinear=False):
    """Andrew 单调链凸包，O(n log n)。

    返回 CCW 顶点列表。共线点策略见模块 docstring。
    """
    pts = sorted(set(points))
    n = len(pts)
    if n <= 2:
        return pts

    def build(seq):
        hull = []
        for p in seq:
            if keep_collinear:
                while len(hull) >= 2 and cross(hull[-2], hull[-1], p) < -EPS:
                    hull.pop()
            else:
                while len(hull) >= 2 and cross(hull[-2], hull[-1], p) <= EPS:
                    hull.pop()
            hull.append(p)
        return hull

    lower = build(pts)
    upper = build(reversed(pts))
    return lower[:-1] + upper[:-1]


def signed_area(poly):
    """多边形有向面积：CCW 为正，CW 为负。"""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def area(poly):
    return abs(signed_area(poly))


def is_ccw(poly):
    return signed_area(poly) > 0


def ensure_ccw(poly):
    """保证多边形为 CCW 朝向。"""
    if len(poly) >= 3 and signed_area(poly) < 0:
        return [poly[0]] + poly[1:][::-1]
    return list(poly)


# ---------------------------------------------------------------- 最远点对

def diameter_pairs(hull):
    """旋转卡壳求凸包直径（最远点对）。

    返回 (距离, [(p, q), ...])：所有达到最大距离的点对。
    输入应为 keep_collinear=False 的凸包（或任意点集，内部先求凸包）。
    """
    h = convex_hull(hull) if len(hull) > 2 else sorted(set(hull))
    n = len(h)
    if n == 0:
        raise ValueError("空点集没有最远点对")
    if n == 1:
        return 0.0, [(h[0], h[0])]
    if n == 2:
        return math.hypot(h[0][0] - h[1][0], h[0][1] - h[1][1]), [(h[0], h[1])]

    best = -1.0
    pairs = []

    def consider(i, j):
        nonlocal best, pairs
        d2 = dist2(h[i], h[j])
        if d2 > best + EPS:
            best = d2
            pairs = [(h[i], h[j])]
        elif abs(d2 - best) <= EPS:
            pairs.append((h[i], h[j]))

    j = 1
    for i in range(n):
        ni = (i + 1) % n
        # 让 j 前进到对边 (i, ni) 的最远点（对踵点）
        while cross(h[i], h[ni], h[(j + 1) % n]) > cross(h[i], h[ni], h[j]) + EPS:
            j = (j + 1) % n
        consider(i, j)
        consider(ni, j)
    return math.sqrt(best), pairs


def diameter_bruteforce(points):
    """暴力 O(n^2) 最远点对，用于对拍。"""
    pts = list(points)
    best = -1.0
    pair = None
    for i in range(len(pts)):
        for j in range(i, len(pts)):
            d2 = dist2(pts[i], pts[j])
            if d2 > best:
                best = d2
                pair = (pts[i], pts[j])
    return math.sqrt(max(best, 0.0)), pair


# ------------------------------------------------------- 最小面积包围矩形

def min_area_rect(hull):
    """旋转卡壳求最小面积包围矩形。

    返回 dict：
        area   : 矩形面积
        width  : 沿 angle 方向的边长
        height : 垂直方向的边长
        angle  : 矩形一条边相对 x 轴的角度（弧度，取自某条凸包边）
        corners: 矩形 4 个角点（CCW）
    点数 < 3 时返回退化矩形（面积 0）。
    """
    h = convex_hull(hull) if len(hull) > 2 else sorted(set(hull))
    n = len(h)
    if n == 0:
        raise ValueError("空点集没有包围矩形")
    if n == 1:
        p = h[0]
        return {"area": 0.0, "width": 0.0, "height": 0.0, "angle": 0.0,
                "corners": [p, p, p, p]}
    if n == 2:
        (x1, y1), (x2, y2) = h
        ang = math.atan2(y2 - y1, x2 - x1)
        w = math.hypot(x2 - x1, y2 - y1)
        return {"area": 0.0, "width": w, "height": 0.0, "angle": ang,
                "corners": [h[0], h[1], h[1], h[0]]}

    # 以第一条边的方向扫描初始化三个卡壳指针，之后随边方向单调旋转。
    ex, ey = h[1][0] - h[0][0], h[1][1] - h[0][1]
    elen = math.hypot(ex, ey)
    u0x, u0y = ex / elen, ey / elen
    v0x, v0y = -u0y, u0x

    def dot0(p, dx, dy):
        return (p[0] - h[0][0]) * dx + (p[1] - h[0][1]) * dy

    j = max(range(n), key=lambda t: dot0(h[t], v0x, v0y))   # 法向最远 -> 高度
    k = max(range(n), key=lambda t: dot0(h[t], u0x, u0y))   # 边向最远 -> 右边界
    l = min(range(n), key=lambda t: dot0(h[t], u0x, u0y))   # 边向最近 -> 左边界

    best = None
    for i in range(n):
        ni = (i + 1) % n
        ex, ey = h[ni][0] - h[i][0], h[ni][1] - h[i][1]
        elen = math.hypot(ex, ey)
        ux, uy = ex / elen, ey / elen      # 边单位向量
        vx, vy = -uy, ux                    # 法向单位向量

        def proj_dot(p):
            return (p[0] - h[i][0]) * ux + (p[1] - h[i][1]) * uy

        def proj_norm(p):
            return (p[0] - h[i][0]) * vx + (p[1] - h[i][1]) * vy

        # 高度最大点
        while proj_norm(h[(j + 1) % n]) > proj_norm(h[j]) + EPS:
            j = (j + 1) % n
        # 边方向投影最大 / 最小点
        while proj_dot(h[(k + 1) % n]) > proj_dot(h[k]) + EPS:
            k = (k + 1) % n
        while proj_dot(h[(l + 1) % n]) < proj_dot(h[l]) - EPS:
            l = (l + 1) % n

        height = proj_norm(h[j])
        width = proj_dot(h[k]) - proj_dot(h[l])
        a = width * height
        if best is None or a < best[0] - EPS:
            ang = math.atan2(uy, ux)
            # 角点：以 h[i] 为原点，沿 u、v 展开
            dmin, dmax = proj_dot(h[l]), proj_dot(h[k])
            ox, oy = h[i]
            c = [
                (ox + ux * dmin, oy + uy * dmin),
                (ox + ux * dmax, oy + uy * dmax),
                (ox + ux * dmax + vx * height, oy + uy * dmax + vy * height),
                (ox + ux * dmin + vx * height, oy + uy * dmin + vy * height),
            ]
            best = (a, width, height, ang, c)

    a, w, ht, ang, corners = best
    return {"area": a, "width": w, "height": ht, "angle": ang,
            "corners": corners}


def min_area_rect_bruteforce(points):
    """暴力法：枚举凸包每条边的方向，旋转后取轴对齐包围盒。用于对拍。

    依据定理：最小面积包围矩形必有一条边与凸包某条边共线。
    """
    h = convex_hull(points)
    n = len(h)
    if n <= 2:
        return min_area_rect(h)["area"]
    best = float("inf")
    for i in range(n):
        x1, y1 = h[i]
        x2, y2 = h[(i + 1) % n]
        ang = math.atan2(y2 - y1, x2 - x1)
        ca, sa = math.cos(-ang), math.sin(-ang)
        xs, ys = [], []
        for (px, py) in h:
            xs.append(px * ca - py * sa)
            ys.append(px * sa + py * ca)
        best = min(best, (max(xs) - min(xs)) * (max(ys) - min(ys)))
    return best
