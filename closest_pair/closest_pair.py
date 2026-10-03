"""最近点对（closest pair of points）。

只依赖 Python 3 标准库。

公开接口：
    closest_pairs(points)        # 分治 O(n log n)
    closest_pairs_bruteforce(points)  # 暴力 O(n^2)，用于对拍

两者返回值一致：
    (min_distance, pairs)
其中 pairs 是所有达到最小距离的点对（距离相同的多组点对全部输出），
每个点对按坐标排序，整体列表也按坐标排序，方便直接比较。
重合的坐标点视为同一个位置：n 个完全重合的点输出 1 个点对，
而不是 C(n,2) 个完全相同的坐标对（否则输出本身就是平方级）。

点数 < 2 时返回 (None, [])。
"""

import math


def _dist2(p, q):
    dx = p[0] - q[0]
    dy = p[1] - q[1]
    return dx * dx + dy * dy


def _normalize(pairs, tag=False):
    """点对去重 + 排序，返回 ((x1,y1),(x2,y1)) 形式的有序列表。"""
    seen = set()
    out = []
    for p, q in pairs:
        a = (p[0], p[1])
        b = (q[0], q[1])
        key = (a, b) if a <= b else (b, a)
        if key not in seen:
            seen.add(key)
            out.append(key)
    out.sort()
    return out


def closest_pairs_bruteforce(points):
    """暴力两两比较，O(n^2)，作为基准实现。"""
    pts = [tuple(p) for p in points]
    if len(pts) < 2:
        return None, []
    best = math.inf
    found = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d2 = _dist2(pts[i], pts[j])
            if d2 < best:
                best = d2
                found = [(pts[i], pts[j])]
            elif d2 == best:
                found.append((pts[i], pts[j]))
    return math.sqrt(best), _normalize(found)


def _brute_rec(px):
    """递归叶子：不超过 3 个点时直接枚举。"""
    best = math.inf
    found = []
    for i in range(len(px)):
        for j in range(i + 1, len(px)):
            d2 = _dist2(px[i], px[j])
            if d2 < best:
                best = d2
                found = [(px[i], px[j])]
            elif d2 == best:
                found.append((px[i], px[j]))
    return best, found


def _merge(best, found, d2, p, q):
    if d2 < best:
        return d2, [(p, q)]
    if d2 == best:
        found.append((p, q))
    return best, found


def _rec(px, py):
    """px: 按 (x,y,id) 排序的点；py: 按 (y,x,id) 排序的同一点集。

    返回 (最小平方距离, 达到该距离的点对列表)。点点带唯一 id，
    保证坐标重合时也能无歧义地按位置切成两半。
    """
    n = len(px)
    if n <= 3:
        return _brute_rec(px)

    mid = n // 2
    left_ids = set()
    for k in range(mid):
        left_ids.add(px[k][2])
    mid_x = px[mid - 1][0]

    left_x = px[:mid]
    right_x = px[mid:]
    left_y = [p for p in py if p[2] in left_ids]
    right_y = [p for p in py if p[2] not in left_ids]

    best, found = _rec(left_x, left_y)
    d2r, found_r = _rec(right_x, right_y)
    if d2r < best:
        best, found = d2r, found_r
    elif d2r == best:
        found.extend(found_r)

    # 带状区域：到分割线的水平距离平方 <= best。
    # 用 <= 而不是 <，因为跨线且距离恰好等于 best 的点对也要收集。
    strip = [p for p in py if (p[0] - mid_x) * (p[0] - mid_x) <= best]
    for i in range(len(strip)):
        for j in range(i + 1, len(strip)):
            dy2 = (strip[j][1] - strip[i][1]) ** 2
            if dy2 > best:
                break
            d2 = _dist2(strip[i], strip[j])
            best, found = _merge(best, found, d2, strip[i], strip[j])

    return best, found


def closest_pairs(points):
    """分治求最近点对，期望/最坏复杂度均为 O(n log n)。

    points: (x, y) 二元组（或可转元组的序列）的可迭代对象。
    返回 (最小欧氏距离, 点对列表)；不足两个点时返回 (None, [])。
    """
    pts = [tuple(p) for p in points]
    if len(pts) < 2:
        return None, []
    tagged = [(x, y, i) for i, (x, y) in enumerate(pts)]
    px = sorted(tagged, key=lambda p: (p[0], p[1], p[2]))
    py = sorted(tagged, key=lambda p: (p[1], p[0], p[2]))
    best2, found = _rec(px, py)
    return math.sqrt(best2), _normalize(found)
