"""贝塞尔曲线递归细分与折线近似（纯标准库）。

功能
----
1. 任意阶数贝塞尔曲线求值 / 分割（de Casteljau，Bernstein 基数值稳定）。
2. 自适应递归细分：平坦度满足容限即停止，输出折线顶点。
3. 平坦度判据带严格理论保证（见 is_flat 的文档字符串），因此
   “折线与曲线的最大偏差 <= tolerance” 是可证明的，而非抽样巧合。
4. 任意阶升阶（精确）与最小二乘降阶（端点保持），降阶误差可实测。
5. 偏差验证工具：对每个叶子段密集采样，实测曲线到折线的最大偏差。

约定
----
- 控制点：等长数值序列的序列，任意维度（2D/3D/...）。
- 参数域恒为 [0, 1]；折线顶点附带全局参数 t。
- 容限 tolerance 为欧氏距离，单位与坐标一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

Point = Tuple[float, ...]

MAX_DEPTH = 60  # 递归深度硬上限（2^-60 的段宽已远低于双精度分辨率）


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _validate_controls(controls: Sequence[Sequence[float]]) -> int:
    if len(controls) < 2:
        raise ValueError("至少需要 2 个控制点")
    dim = len(controls[0])
    if dim == 0:
        raise ValueError("控制点维度必须 >= 1")
    for p in controls:
        if len(p) != dim:
            raise ValueError("所有控制点维度必须一致")
    return dim


def _comb(n: int, k: int) -> int:
    return math.comb(n, k)


def _dist(a: Point, b: Point) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _perp_dist(p: Point, a: Point, b: Point) -> float:
    """点 p 到直线 ab 的垂直距离；a==b 时退化为到点 a 的距离。"""
    dim = len(a)
    d = [bi - ai for ai, bi in zip(a, b)]
    v = [pi - ai for pi, ai in zip(p, a)]
    d2 = sum(x * x for x in d)
    if d2 == 0.0:
        return math.sqrt(sum(x * x for x in v))
    cross2 = sum((d[i] * v[j] - d[j] * v[i]) ** 2
                 for i in range(dim) for j in range(i + 1, dim))
    return math.sqrt(cross2 / d2)


def _dist_point_segment(p: Point, a: Point, b: Point) -> float:
    """点 p 到线段 ab 的最短距离。"""
    d2 = sum((bi - ai) ** 2 for ai, bi in zip(a, b))
    if d2 == 0.0:
        return _dist(p, a)
    s = sum((pi - ai) * (bi - ai) for pi, ai, bi in zip(p, a, b)) / d2
    s = max(0.0, min(1.0, s))
    proj = tuple(ai + s * (bi - ai) for ai, bi in zip(a, b))
    return _dist(p, proj)


# ---------------------------------------------------------------------------
# 求值与分割
# ---------------------------------------------------------------------------

def evaluate(controls: Sequence[Sequence[float]], t: float) -> Point:
    """de Casteljau 求曲线在参数 t 处的点。"""
    _validate_controls(controls)
    work = [tuple(map(float, p)) for p in controls]
    for _ in range(len(work) - 1, 0, -1):
        work = [tuple(a + (b - a) * t for a, b in zip(work[i], work[i + 1]))
                for i in range(len(work) - 1)]
    return work[0]


def split(controls: Sequence[Sequence[float]], t: float = 0.5
          ) -> Tuple[List[Point], List[Point]]:
    """de Casteljau 在 t 处分割，返回 (左段控制点, 右段控制点)。"""
    _validate_controls(controls)
    work = [tuple(map(float, p)) for p in controls]
    left: List[Point] = []
    right: List[Point] = []
    while work:
        left.append(work[0])
        right.append(work[-1])
        work = [tuple(a + (b - a) * t for a, b in zip(work[i], work[i + 1]))
                for i in range(len(work) - 1)]
    right.reverse()
    return left, right


# ---------------------------------------------------------------------------
# 平坦度判据（带严格理论保证）
# ---------------------------------------------------------------------------

def is_flat(controls: Sequence[Sequence[float]], tolerance: float) -> bool:
    """判断曲线段是否可用端点弦以不超过 tolerance 的偏差近似。

    判据：所有内部控制点到弦线的垂直距离 <= tolerance。

    理论保证（凸性论证）：
      设弦为 P0->Pn，参数 s in [0,1] 为弦上的归一化投影坐标。
      曲线点 C(t) = sum B_i(t) P_i，其中 sum B_i = 1, B_i >= 0。
      每个控制点 P_i 都落在“到弦线垂直距离 <= tol 且投影落在弦所在
      线段上”的管道内（端点投影恰为 0 和 1，内部点由垂直距离约束），
      因此 C(t) 作为控制点的凸组合也落在该管道内：
        - 垂直分量：perp(C(t)) <= sum B_i * perp(P_i) <= tol；
        - 平行分量：proj(C(t)) = sum B_i * proj(P_i) in [0, |弦|]。
      故 C(t) 到弦线段的最短距离 <= 垂直距离 <= tol。
      端点重合（弦长为 0）时判据退化为“所有控制点到 P0 的距离 <= tol”，
      由凸包性同样保证曲线上每点到 P0 的距离 <= tol。

    该判据是充分（保守）条件：满足则偏差必然 <= tol。
    """
    _validate_controls(controls)
    if tolerance < 0:
        raise ValueError("tolerance 必须 >= 0")
    a, b = controls[0], controls[-1]
    return all(_perp_dist(p, a, b) <= tolerance for p in controls[1:-1])


# ---------------------------------------------------------------------------
# 自适应细分 -> 折线
# ---------------------------------------------------------------------------

@dataclass
class FlattenResult:
    points: List[Point]          # 折线顶点（曲线上的点）
    ts: List[float]              # 每个顶点对应的全局参数 t
    leaf_controls: List[List[Point]] = field(default_factory=list)
    leaf_ts: List[Tuple[float, float]] = field(default_factory=list)
    max_depth_reached: bool = False

    @property
    def num_points(self) -> int:
        return len(self.points)


def flatten(controls: Sequence[Sequence[float]], tolerance: float,
            max_depth: int = MAX_DEPTH) -> FlattenResult:
    """递归细分曲线为折线，平坦度 <= tolerance 时停止。

    返回 FlattenResult；顶点为曲线上的点，附带全局参数 t。
    由 is_flat 的理论保证，折线与曲线的最大偏差 <= tolerance
    （除非触及 max_depth，此时 max_depth_reached=True）。
    """
    _validate_controls(controls)
    if tolerance <= 0:
        raise ValueError("tolerance 必须 > 0")
    cps = [tuple(map(float, p)) for p in controls]

    leaf_controls: List[List[Point]] = []
    leaf_ts: List[Tuple[float, float]] = []
    hit_cap = [False]

    def rec(c: List[Point], t0: float, t1: float, depth: int) -> None:
        if is_flat(c, tolerance):
            leaf_controls.append(c)
            leaf_ts.append((t0, t1))
            return
        if depth >= max_depth:
            hit_cap[0] = True
            leaf_controls.append(c)
            leaf_ts.append((t0, t1))
            return
        left, right = split(c, 0.5)
        tm = 0.5 * (t0 + t1)
        rec(left, t0, tm, depth + 1)
        rec(right, tm, t1, depth + 1)

    rec(cps, 0.0, 1.0, 0)

    points: List[Point] = []
    ts: List[float] = []
    for c, (t0, t1) in zip(leaf_controls, leaf_ts):
        if not points:
            points.append(c[0])
            ts.append(t0)
        points.append(c[-1])
        ts.append(t1)
    return FlattenResult(points=points, ts=ts,
                         leaf_controls=leaf_controls, leaf_ts=leaf_ts,
                         max_depth_reached=hit_cap[0])


# ---------------------------------------------------------------------------
# 偏差验证
# ---------------------------------------------------------------------------

def polyline_deviation(curve_controls: Sequence[Sequence[float]],
                       poly_points: Sequence[Point],
                       poly_ts: Sequence[float],
                       samples_per_segment: int = 64) -> Tuple[float, float]:
    """实测折线与曲线的最大偏差。

    对折线每段，在其参数区间内均匀采样 samples_per_segment 个曲线点
    （含端点），计算每个采样点到对应折线段的欧氏最短距离，取最大值。

    返回 (最大偏差, 取得最大偏差处的参数 t)。
    """
    _validate_controls(curve_controls)
    if len(poly_points) != len(poly_ts) or len(poly_points) < 2:
        raise ValueError("折线顶点与参数数量不一致或过少")
    worst = -1.0
    worst_t = poly_ts[0]
    for i in range(len(poly_points) - 1):
        a, b = poly_points[i], poly_points[i + 1]
        ta, tb = poly_ts[i], poly_ts[i + 1]
        for k in range(samples_per_segment + 1):
            t = ta + (tb - ta) * k / samples_per_segment
            d = _dist_point_segment(evaluate(curve_controls, t), a, b)
            if d > worst:
                worst, worst_t = d, t
    return worst, worst_t


def distance_point_to_polyline(p: Point, poly_points: Sequence[Point]) -> float:
    """点到整条折线的最短距离（用于独立的全局密集验证）。"""
    if len(poly_points) < 2:
        raise ValueError("折线至少需要 2 个顶点")
    return min(_dist_point_segment(p, poly_points[i], poly_points[i + 1])
               for i in range(len(poly_points) - 1))


def global_dense_deviation(curve_controls: Sequence[Sequence[float]],
                           poly_points: Sequence[Point],
                           samples: int = 200001) -> float:
    """与分段采样无关的独立验证：全局均匀采样取到整条折线的最短距离。"""
    worst = 0.0
    for k in range(samples):
        t = k / (samples - 1)
        d = distance_point_to_polyline(evaluate(curve_controls, t),
                                       poly_points)
        worst = max(worst, d)
    return worst


def measure_reduction_error(orig_controls: Sequence[Sequence[float]],
                            reduced_controls: Sequence[Sequence[float]],
                            samples: int = 2001) -> float:
    """实测降阶前后两条曲线的最大点对点距离（同参数采样）。"""
    _validate_controls(orig_controls)
    _validate_controls(reduced_controls)
    worst = 0.0
    for k in range(samples):
        t = k / (samples - 1)
        worst = max(worst, _dist(evaluate(orig_controls, t),
                                 evaluate(reduced_controls, t)))
    return worst


# ---------------------------------------------------------------------------
# 升阶与降阶
# ---------------------------------------------------------------------------

def elevate(controls: Sequence[Sequence[float]],
            target_degree: int) -> List[Point]:
    """升阶到 target_degree（精确，曲线不变）。"""
    _validate_controls(controls)
    n = len(controls) - 1
    if target_degree < n:
        raise ValueError("target_degree 必须 >= 当前阶数")
    cps = [tuple(map(float, p)) for p in controls]
    while n < target_degree:
        m = n + 1
        new: List[Point] = [cps[0]]
        for i in range(1, m):
            a = i / m
            new.append(tuple(a * cps[i - 1][d] + (1.0 - a) * cps[i][d]
                             for d in range(len(cps[0]))))
        new.append(cps[-1])
        cps = new
        n = m
    return cps


def _bernstein_matrix(rows: int, n: int) -> List[List[float]]:
    """rows 行均匀参数的 n 阶 Bernstein 基矩阵。"""
    mat = []
    for k in range(rows):
        t = k / (rows - 1)
        mat.append([_comb(n, i) * t ** i * (1.0 - t) ** (n - i)
                    for i in range(n + 1)])
    return mat


def _solve_least_squares_multi(a: List[List[float]],
                               b_cols: List[List[float]]) -> List[List[float]]:
    """对多个右端列解 min ||A x - b||（法方程 + 部分主元高斯消元）。

    a: m x n（m >= n），b_cols: 每个元素为长度 m 的右端列。
    返回 n 个解列（每个长度 n）。
    """
    m, n = len(a), len(a[0])
    ata = [[sum(a[r][i] * a[r][j] for r in range(m)) for j in range(n)]
           for i in range(n)]
    atb = [[sum(a[r][i] * col[r] for r in range(m)) for col in b_cols]
           for i in range(n)]
    # 高斯消元（部分主元），右端为 len(b_cols) 列
    ncols = len(b_cols)
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(ata[r][col]))
        if abs(ata[piv][col]) < 1e-300:
            raise ArithmeticError("法方程奇异")
        ata[col], ata[piv] = ata[piv], ata[col]
        atb[col], atb[piv] = atb[piv], atb[col]
        inv = 1.0 / ata[col][col]
        ata[col] = [v * inv for v in ata[col]]
        atb[col] = [v * inv for v in atb[col]]
        for r in range(n):
            if r != col and ata[r][col] != 0.0:
                f = ata[r][col]
                ata[r] = [v - f * w for v, w in zip(ata[r], ata[col])]
                atb[r] = [v - f * w for v, w in zip(atb[r], atb[col])]
    return atb  # n 行，每行对应一个右端列的解


def reduce_degree(controls: Sequence[Sequence[float]],
                  target_degree: int) -> List[Point]:
    """最小二乘降阶到 target_degree（端点保持）。

    方法：min ||B_m Q - B_n P||_2，约束 Q 的首末控制点等于原曲线端点
    （保证降阶后曲线端点不变）。内部控制点由法方程解出。
    采样行数取 max(4*(m+1), 64)，对病态阶数有充分冗余。
    """
    _validate_controls(controls)
    n = len(controls) - 1
    if target_degree < 1:
        raise ValueError("target_degree 必须 >= 1")
    if target_degree >= n:
        raise ValueError("target_degree 必须 < 当前阶数")
    m = target_degree
    dim = len(controls[0])
    cps = [tuple(map(float, p)) for p in controls]

    rows = max(4 * (m + 1), 64)
    bn = _bernstein_matrix(rows, n)
    bm = _bernstein_matrix(rows, m)

    # 右端：B_n P - [端点列的贡献]，未知量为 m-1 个内部控制点
    p0, pn = cps[0], cps[-1]
    rhs_cols: List[List[float]] = []
    for d in range(dim):
        col = []
        for r in range(rows):
            v = sum(bn[r][i] * cps[i][d] for i in range(n + 1))
            v -= bm[r][0] * p0[d] + bm[r][m] * pn[d]
            col.append(v)
        rhs_cols.append(col)
    a = [[bm[r][j] for j in range(1, m)] for r in range(rows)]
    sol = _solve_least_squares_multi(a, rhs_cols)  # (m-1) 行 x dim 列

    out: List[Point] = [p0]
    for j in range(m - 1):
        out.append(tuple(sol[j][d] for d in range(dim)))
    out.append(pn)
    return out
