"""最近点对：分治算法（O(n log n)），仅使用 Python 标准库。

公开接口
--------
closest_pair(points)  分治实现，返回 ClosestPair 命名元组
brute_force(points)   O(n^2) 暴力实现，供对拍，返回同样的结构
all_pairs(result)     惰性迭代结果中的全部最近点对 (i, j)，i < j

返回结构 ClosestPair(distance, pairs, groups)
---------------------------------------------
distance : float
    最近欧氏距离；点数 < 2 时为 inf。
pairs : tuple
    全部取得最近距离的点对，元素为原始下标对 (i, j)，i < j，升序排列。
    当最近距离为 0（存在重合点）时此字段为空，改用 groups 表示。
groups : tuple
    重合点分组：每组是同一坐标下全部原始下标的升序元组。
    距离为 0 时，所有最近点对即各组内部下标的全部组合（C(k,2) 个），
    数量可能达到平方级，故不物化，请用 all_pairs() 惰性枚举。

坐标支持 int / float。int 坐标全程精确运算；float 遵循 IEEE 754，
坐标差小于该量级 ULP 时会被舍入为同一坐标（即视为重合）。
"""

from collections import defaultdict
from math import inf, sqrt
from typing import Iterator, NamedTuple, Sequence, Tuple

__all__ = ["ClosestPair", "closest_pair", "brute_force", "all_pairs"]


class ClosestPair(NamedTuple):
    distance: float
    pairs: Tuple[Tuple[int, int], ...]
    groups: Tuple[Tuple[int, ...], ...]


def all_pairs(result: ClosestPair) -> Iterator[Tuple[int, int]]:
    """惰性迭代全部最近点对 (i, j)，i < j（含重合组的组合展开）。"""
    for g in result.groups:
        m = len(g)
        for a in range(m):
            for b in range(a + 1, m):
                yield (g[a], g[b])
    yield from result.pairs


def _key(i, j):
    return (i, j) if i < j else (j, i)


def _sq_dist(p, q):
    dx = p[1] - q[1]
    dy = p[2] - q[2]
    return dx * dx + dy * dy


def _brute(pts):
    """pts: [(idx, x, y), ...]，返回 (best_sq, {(i, j), ...})，i<j。"""
    best = inf
    pairs = set()
    m = len(pts)
    for a in range(m):
        p = pts[a]
        for b in range(a + 1, m):
            d = _sq_dist(p, pts[b])
            if d < best:
                best = d
                pairs = {_key(p[0], pts[b][0])}
            elif d == best:
                pairs.add(_key(p[0], pts[b][0]))
    return best, pairs


def _solve(px):
    """分治主体。

    px: 按 (x, y) 排序的 [(idx, x, y), ...]。
    返回 (best_sq, pairs, py)，py 为按 (y, x) 排序的同批点（供上层归并）。
    """
    n = len(px)

    if n <= 3:
        best, pairs = _brute(px)
        return best, pairs, sorted(px, key=lambda p: (p[2], p[1]))

    mid = n // 2
    midx = px[mid][1]

    best_l, pairs_l, pyl = _solve(px[:mid])
    best_r, pairs_r, pyr = _solve(px[mid:])

    if best_l < best_r:
        best, pairs = best_l, set(pairs_l)
    elif best_r < best_l:
        best, pairs = best_r, set(pairs_r)
    else:
        best, pairs = best_l, pairs_l | pairs_r

    # 归并两个按 y 排序的序列
    py = []
    i = j = 0
    while i < len(pyl) and j < len(pyr):
        if (pyl[i][2], pyl[i][1]) <= (pyr[j][2], pyr[j][1]):
            py.append(pyl[i])
            i += 1
        else:
            py.append(pyr[j])
            j += 1
    py.extend(pyl[i:])
    py.extend(pyr[j:])

    # 距分界线不超过当前最优距离的条带（按 y 排序后只需常数次比较）
    strip = [(0, p) for p in pyl if (p[1] - midx) * (p[1] - midx) <= best]
    strip += [(1, p) for p in pyr if (p[1] - midx) * (p[1] - midx) <= best]
    strip.sort(key=lambda t: (t[1][2], t[1][1]))

    for a in range(len(strip)):
        side_p, p = strip[a]
        for b in range(a + 1, len(strip)):
            side_q, q = strip[b]
            dy = q[2] - p[2]
            if dy * dy > best:
                break
            if side_p == side_q:
                continue  # 同侧点对已在子问题中统计
            d = _sq_dist(p, q)
            if d < best:
                best = d
                pairs = {_key(p[0], q[0])}
            elif d == best:
                pairs.add(_key(p[0], q[0]))

    return best, pairs, py


def _dup_groups(pts):
    """重合点分组：[(x, y)] -> 每组坐标相同的下标元组（升序），仅含 >=2 的组。"""
    by_coord = defaultdict(list)
    for p in pts:
        by_coord[(p[1], p[2])].append(p[0])
    return tuple(
        tuple(sorted(idxs)) for idxs in by_coord.values() if len(idxs) >= 2
    )


def _wrap(points):
    return [(i, x, y) for i, (x, y) in enumerate(points)]


def closest_pair(points: Sequence[Tuple[float, float]]) -> ClosestPair:
    """分治求最近点对，返回 ClosestPair(distance, pairs, groups)。"""
    points = list(points)
    if len(points) < 2:
        return ClosestPair(inf, (), ())

    pts = _wrap(points)

    # 重合点：距离 0 一旦出现即为全局最优，直接分组返回，不物化点对。
    groups = _dup_groups(pts)
    if groups:
        return ClosestPair(0.0, (), groups)

    px = sorted(pts, key=lambda p: (p[1], p[2]))
    best_sq, pairs, _ = _solve(px)
    return ClosestPair(sqrt(best_sq), tuple(sorted(pairs)), ())


def brute_force(points: Sequence[Tuple[float, float]]) -> ClosestPair:
    """O(n^2) 暴力最近点对，语义与 closest_pair 完全一致，用于对拍。"""
    points = list(points)
    if len(points) < 2:
        return ClosestPair(inf, (), ())

    pts = _wrap(points)
    groups = _dup_groups(pts)
    if groups:
        return ClosestPair(0.0, (), groups)

    best_sq, pairs = _brute(pts)
    return ClosestPair(sqrt(best_sq), tuple(sorted(pairs)), ())
