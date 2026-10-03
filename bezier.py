"""贝塞尔曲线细分与折线近似库（仅标准库）。

核心内容：
- de Casteljau 求值与中点递归细分；
- 基于平坦度的自适应停止（控制多边形到弦的距离给出偏差上界）；
- 任意阶数支持、最小二乘降阶及降阶误差界；
- 实测偏差验证工具（密集采样 + 点到线段距离）。
"""

import math
from math import comb

# ---------------------------------------------------------------------------
# 基础运算：de Casteljau
# ---------------------------------------------------------------------------

def _blend(a, b, t):
    return tuple((1.0 - t) * a[d] + t * b[d] for d in range(len(a)))


def evaluate(ctrl, t):
    """de Casteljau 求曲线上参数 t 处的点。"""
    level = [tuple(p) for p in ctrl]
    while len(level) > 1:
        level = [_blend(level[i], level[i + 1], t) for i in range(len(level) - 1)]
    return level[0]


def split(ctrl, t=0.5):
    """在参数 t 处把曲线切成两段，返回 (左控制多边形, 右控制多边形)。"""
    level = [tuple(p) for p in ctrl]
    left = [level[0]]
    right = [level[-1]]
    while len(level) > 1:
        level = [_blend(level[i], level[i + 1], t) for i in range(len(level) - 1)]
        left.append(level[0])
        right.append(level[-1])
    right.reverse()
    return left, right


# ---------------------------------------------------------------------------
# 平坦度与自适应细分
# ---------------------------------------------------------------------------

def flatness(ctrl):
    """控制多边形相对端点弦的最大“参数化”距离。

    对每个内控制点 P_i 计算它到弦上 L(i/n) 点的距离（n 为阶数，
    L(s)=(1-s)P0+sPn）。曲线到弦的偏差是这些向量的凸组合，因此
    flatness 是 |C(t)-L(t)| 的严格上界，可直接当作停止容限使用。
    端点重合（弦退化为点）时退化为到 P0 的最大距离。
    """
    n = len(ctrl) - 1
    p0 = ctrl[0]
    pn = ctrl[-1]
    worst = 0.0
    for i in range(1, n):
        s = i / n
        chord = tuple((1.0 - s) * p0[d] + s * pn[d] for d in range(len(p0)))
        dist = math.sqrt(sum((ctrl[i][d] - chord[d]) ** 2
                             for d in range(len(p0))))
        if dist > worst:
            worst = dist
    return worst


def flatten_segments(ctrl, tol, max_depth=64):
    """递归细分，返回满足 flatness <= tol 的叶子段控制多边形列表。"""
    if tol <= 0.0:
        raise ValueError("tol 必须为正数")
    leaves = []

    def recurse(points, depth):
        if depth >= max_depth or flatness(points) <= tol:
            leaves.append(points)
            return
        left, right = split(points)
        recurse(left, depth + 1)
        recurse(right, depth + 1)

    recurse([tuple(p) for p in ctrl], 0)
    return leaves


def flatten(ctrl, tol, max_depth=64):
    """自适应细分，返回折线顶点列表（首尾包含曲线端点）。"""
    leaves = flatten_segments(ctrl, tol, max_depth)
    polyline = [leaves[0][0]]
    polyline.extend(leaf[-1] for leaf in leaves)
    return polyline


# ---------------------------------------------------------------------------
# 阶数提升 / 降阶
# ---------------------------------------------------------------------------

def elevate(ctrl, times=1):
    """贝塞尔阶数提升 times 次（升阶后曲线完全不变）。"""
    pts = [tuple(p) for p in ctrl]
    for _ in range(times):
        n = len(pts) - 1
        raised = [pts[0]]
        for j in range(1, n + 1):
            a = j / (n + 1.0)
            raised.append(tuple(a * pts[j - 1][d] + (1.0 - a) * pts[j][d]
                                for d in range(len(pts[0]))))
        raised.append(pts[-1])
        pts = raised
    return pts


def _gram(degree):
    """伯恩斯坦基函数 Gram 矩阵 G[j][k]=∫₀¹ B_j^d(t) B_k^d(t) dt。"""
    size = degree + 1
    return [
        [
            comb(degree, j) * comb(degree, k)
            / ((2 * degree + 1) * comb(2 * degree, j + k))
            for k in range(size)
        ]
        for j in range(size)
    ]


def _solve_linear_system(matrix, vector):
    """高斯消元（部分选主元）解线性方程组。"""
    n = len(matrix)
    a = [row[:] for row in matrix]
    b = vector[:]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-15:
            raise ValueError("方程组奇异，无法降阶")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]
        for row in range(col + 1, n):
            factor = a[row][col] / a[col][col]
            a[row][col] = 0.0
            for k in range(col + 1, n):
                a[row][k] -= factor * a[col][k]
            b[row] -= factor * b[col]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (b[i] - sum(a[i][k] * x[k] for k in range(i + 1, n))) / a[i][i]
    return x


def reduce_degree(ctrl, target_degree):
    """最小二乘降阶到 target_degree（< 当前阶数）。

    在 L2[0,1] 意义下最小化 ∫|原曲线-降阶曲线|² dt，利用伯恩斯坦基
    内积公式构造法方程求解，支持任意阶数。
    """
    pts = [tuple(p) for p in ctrl]
    n = len(pts) - 1
    m = target_degree
    if m >= n:
        raise ValueError("目标阶数必须小于当前阶数")
    dim = len(pts[0])
    gram = _gram(m)

    result_per_dim = []
    for d in range(dim):
        rhs = [0.0] * (m + 1)
        for j in range(m + 1):
            total = 0.0
            for i in range(n + 1):
                inner = (comb(n, i) * comb(m, j)
                         / ((n + m + 1) * comb(n + m, i + j)))
                total += pts[i][d] * inner
            rhs[j] = total
        result_per_dim.append(_solve_linear_system(gram, rhs))

    return [
        tuple(result_per_dim[d][j] for d in range(dim))
        for j in range(m + 1)
    ]


def degree_reduction_error_bound(ctrl, reduced):
    """降阶误差上界：把降阶曲线升回原阶数，取控制点差的最大范数。

    两曲线之差仍是同阶贝塞尔曲线，其取值落在控制点差向量的凸包内，
    故 max_t |C(t)-R(t)| <= max_i |P_i - (升阶后的 Q)_i|。
    """
    n = len(ctrl) - 1
    m = len(reduced) - 1
    raised = elevate(reduced, n - m)
    return max(
        math.sqrt(sum((ctrl[i][d] - raised[i][d]) ** 2
                      for d in range(len(ctrl[0]))))
        for i in range(n + 1)
    )


# ---------------------------------------------------------------------------
# 实测偏差
# ---------------------------------------------------------------------------

def _point_segment_dist(point, seg_a, seg_b):
    """点到线段的最近距离。"""
    dim = len(point)
    v = tuple(seg_b[d] - seg_a[d] for d in range(dim))
    w = tuple(point[d] - seg_a[d] for d in range(dim))
    length_sq = sum(x * x for x in v)
    if length_sq == 0.0:
        return math.sqrt(sum(x * x for x in w))
    ratio = max(0.0, min(1.0, sum(w[d] * v[d] for d in range(dim)) / length_sq))
    return math.sqrt(
        sum((w[d] - ratio * v[d]) ** 2 for d in range(dim))
    )


def polyline_deviation(ctrl, polyline, samples_per_segment=64):
    """密集采样曲线，返回折线到曲线的实测最大偏差。

    采样点与折线都沿曲线单调排列，最近线段的索引单调不减，因此用
    前向指针做近线性扫描；同时检查前后各 2 条线段，以兜底曲线自交
    或折返时最近线段短暂回跳的情况。
    """
    segments = list(zip(polyline[:-1], polyline[1:]))
    if not segments:
        return 0.0
    total_steps = samples_per_segment * len(segments)
    worst = 0.0
    seg_index = 0
    for k in range(total_steps + 1):
        point = evaluate(ctrl, k / total_steps)
        best = _point_segment_dist(point, *segments[seg_index])
        while seg_index + 1 < len(segments):
            dist = _point_segment_dist(point, *segments[seg_index + 1])
            if dist < best:
                best = dist
                seg_index += 1
            else:
                break
        for j in range(max(0, seg_index - 2), seg_index):
            dist = _point_segment_dist(point, *segments[j])
            if dist < best:
                best = dist
                seg_index = j
        if best > worst:
            worst = best
    return worst


def leaf_deviation(leaf, samples=64):
    """单个细分叶子段：曲线到其弦的实测最大偏差。"""
    a, b = leaf[0], leaf[-1]
    worst = 0.0
    for k in range(samples + 1):
        point = evaluate(leaf, k / samples)
        dist = _point_segment_dist(point, a, b)
        if dist > worst:
            worst = dist
    return worst


def measured_flatten_deviation(ctrl, tol, samples_per_leaf=64):
    """对细分结果逐段实测最大偏差（不依赖折线参数映射，效率高）。"""
    leaves = flatten_segments(ctrl, tol)
    return max(leaf_deviation(leaf, samples_per_leaf) for leaf in leaves), leaves


def curve_to_curve_deviation(ctrl_a, ctrl_b, samples=2000):
    """两条曲线（同参数域）之间的实测最大距离，用于验证降阶误差。"""
    worst = 0.0
    for k in range(samples + 1):
        t = k / samples
        pa = evaluate(ctrl_a, t)
        pb = evaluate(ctrl_b, t)
        dist = math.sqrt(sum((pa[d] - pb[d]) ** 2 for d in range(len(pa))))
        if dist > worst:
            worst = dist
    return worst
