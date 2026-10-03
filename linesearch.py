"""linesearch.py — 线搜索与步长选择库（仅标准库）

实现两种线搜索，均保证 Armijo 充分下降条件：

1. backtracking_line_search —— 回溯线搜索
   终止条件：
     成功：phi(alpha) <= phi0 + c1 * alpha * dphi0  （Armijo 充分下降）
     失败：alpha 收缩到 alpha_min 以下（返回 converged=False，不抛异常、不死循环）
   步长选择：alpha <- rho * alpha，几何收缩，必然终止。

2. interpolation_line_search —— 插值线搜索
   终止条件：与回溯相同（Armijo 充分下降 / alpha < alpha_min 失败兜底）。
   步长选择：
     第一次失败用二次插值（利用 phi(0), phi'(0), phi(alpha)）；
     之后用三次 Hermite 插值（利用 phi(0), phi'(0) 与最近两个试探点的函数值），
     并做安全裁剪：新步长落在 [shrink_lo*alpha, shrink_hi*alpha] 内，
     保证每次至少按 shrink_hi 比例收缩，因此同样必然终止。

梯度下降驱动 gradient_descent：
  - 每轮迭代用线搜索求步长，断言目标值严格单调下降（Armijo 蕴含 f_new < f_cur）。
  - bounds 参数支持盒式约束（边界受限问题）：方向取单位投影步方向
    d = clip(x - g, lo, hi) - x，步长上限 1，仍是严格下降方向。
"""

from dataclasses import dataclass, field
from math import sqrt


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------

@dataclass
class LineSearchResult:
    alpha: float          # 最终步长
    f_new: float          # phi(alpha)
    n_fevals: int         # 本次线搜索的函数评估次数
    converged: bool       # 是否满足 Armijo 充分下降条件
    message: str = ""


@dataclass
class OptimizeResult:
    x: list
    f: float
    n_iter: int           # 外层迭代次数
    n_fevals: int         # 总函数评估次数（含线搜索）
    converged: bool
    message: str
    f_history: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _dot(a, b):
    return sum(ai * bi for ai, bi in zip(a, b))


def _norm(a):
    return sqrt(_dot(a, a))


def assert_monotone_decreasing(values, tol=1e-12):
    """断言序列单调不增（允许 tol 级浮点误差），且至少严格下降一次。

    每轮迭代后调用可验证“目标值单调下降”这一性质。
    """
    if len(values) < 2:
        return
    for k in range(1, len(values)):
        prev, cur = values[k - 1], values[k]
        scale = max(1.0, abs(prev))
        assert cur <= prev + tol * scale, (
            f"单调下降被破坏: f[{k}]={cur!r} > f[{k-1}]={prev!r}"
        )


# ---------------------------------------------------------------------------
# 1. 回溯线搜索
# ---------------------------------------------------------------------------

def backtracking_line_search(phi, phi0, dphi0, alpha0=1.0, *,
                             c1=1e-4, rho=0.5,
                             alpha_min=1e-16, max_iter=100):
    """回溯线搜索。

    参数:
        phi    : 一维函数 phi(alpha) -> f(x + alpha*d)
        phi0   : phi(0)
        dphi0  : phi'(0)，必须为负（下降方向），否则 ValueError
        alpha0 : 初始步长
        c1     : Armijo 常数，0 < c1 < 1
        rho    : 收缩因子，0 < rho < 1

    终止条件:
        成功: phi(alpha) <= phi0 + c1*alpha*dphi0（Armijo 充分下降）
        失败: alpha < alpha_min 或超过 max_iter 次收缩
    """
    if dphi0 >= 0:
        raise ValueError(f"方向非下降方向: dphi0={dphi0!r} >= 0")
    if not (0.0 < c1 < 1.0) or not (0.0 < rho < 1.0):
        raise ValueError("需要 0 < c1 < 1 且 0 < rho < 1")

    alpha = alpha0
    for k in range(max_iter):
        phi_a = phi(alpha)
        if phi_a <= phi0 + c1 * alpha * dphi0:      # Armijo 充分下降
            return LineSearchResult(alpha, phi_a, k + 1, True, "armijo")
        alpha *= rho
        if alpha < alpha_min:
            return LineSearchResult(alpha, phi_a, k + 1, False,
                                    "alpha < alpha_min，线搜索失败")
    return LineSearchResult(alpha, phi_a, max_iter, False,
                            "达到 max_iter，线搜索失败")


# ---------------------------------------------------------------------------
# 2. 插值线搜索
# ---------------------------------------------------------------------------

def _quadratic_min(phi0, dphi0, alpha, phi_a):
    """由 phi(0), phi'(0), phi(alpha) 构造二次模型的极小点。"""
    denom = 2.0 * (phi_a - phi0 - dphi0 * alpha)
    if denom <= 0.0:                    # 数值退化，退化为折半
        return 0.5 * alpha
    return -dphi0 * alpha * alpha / denom


def _cubic_min(phi0, dphi0, a0, phi_a0, a1, phi_a1):
    """由 phi(0), phi'(0) 与两点函数值 phi(a0), phi(a1) 构造三次模型极小点。

    参考 Nocedal & Wright, Numerical Optimization, eq. (3.59)。
    退化时返回 None，由调用方回退到二次插值。
    """
    A = phi_a0 - phi0 - dphi0 * a0
    B = phi_a1 - phi0 - dphi0 * a1
    denom = a0 * a0 * a1 * a1 * (a1 - a0)
    if denom == 0.0:
        return None
    c = (A * a1**3 - B * a0**3) / denom
    d = (B * a0 * a0 - A * a1 * a1) / denom
    disc = c * c - 3.0 * d * dphi0
    if d == 0.0 or disc < 0.0:
        return None
    return (-c + sqrt(disc)) / (3.0 * d)


def interpolation_line_search(phi, phi0, dphi0, alpha0=1.0, *,
                              c1=1e-4, shrink_lo=0.1, shrink_hi=0.5,
                              alpha_min=1e-16, max_iter=100):
    """基于二次/三次插值的线搜索。

    终止条件:
        成功: phi(alpha) <= phi0 + c1*alpha*dphi0（Armijo 充分下降）
        失败: alpha < alpha_min 或超过 max_iter
    步长选择:
        首次失败 -> 安全裁剪的二次插值极小点；
        后续失败 -> 安全裁剪的三次插值极小点（退化时回退二次）。
        安全裁剪保证新步长 <= shrink_hi * alpha，故必在有限步内终止。
    """
    if dphi0 >= 0:
        raise ValueError(f"方向非下降方向: dphi0={dphi0!r} >= 0")
    if not (0.0 < c1 < 1.0):
        raise ValueError("需要 0 < c1 < 1")
    if not (0.0 < shrink_lo < shrink_hi < 1.0):
        raise ValueError("需要 0 < shrink_lo < shrink_hi < 1")

    alpha = alpha0
    alpha_prev, phi_prev = 0.0, phi0
    phi_a = phi0
    for k in range(max_iter):
        phi_a = phi(alpha)
        if phi_a <= phi0 + c1 * alpha * dphi0:      # Armijo 充分下降
            return LineSearchResult(alpha, phi_a, k + 1, True, "armijo")

        if k == 0:
            alpha_new = _quadratic_min(phi0, dphi0, alpha, phi_a)
        else:
            alpha_new = _cubic_min(phi0, dphi0, alpha_prev, phi_prev,
                                   alpha, phi_a)
            if alpha_new is None:
                alpha_new = _quadratic_min(phi0, dphi0, alpha, phi_a)

        # 安全裁剪：保证充分收缩，避免插值点贴边导致停滞
        alpha_new = min(max(alpha_new, shrink_lo * alpha),
                        shrink_hi * alpha)

        alpha_prev, phi_prev = alpha, phi_a
        alpha = alpha_new
        if alpha < alpha_min:
            return LineSearchResult(alpha, phi_a, k + 1, False,
                                    "alpha < alpha_min，线搜索失败")
    return LineSearchResult(alpha, phi_a, max_iter, False,
                            "达到 max_iter，线搜索失败")


LINE_SEARCHES = {
    "backtracking": backtracking_line_search,
    "interpolation": interpolation_line_search,
}


# ---------------------------------------------------------------------------
# 3. 梯度下降驱动（支持盒式约束，内置单调下降断言）
# ---------------------------------------------------------------------------

def gradient_descent(f, grad, x0, *, method="interpolation",
                     alpha0=1.0, alpha_growth=2.0,
                     tol=1e-8, max_iter=10000,
                     bounds=None, c1=1e-4):
    """带线搜索的梯度下降。

    参数:
        f, grad : 目标函数与梯度，均接收 list[float]
        x0      : 初始点
        method  : "backtracking" 或 "interpolation"
        alpha0  : 首轮初始步长；之后每轮以上一轮接受步长的 alpha_growth 倍起步
        tol     : 梯度范数收敛容差（有约束时为投影步方向范数）
        bounds  : None 或 (lo, hi) 列表对，盒式约束 lo[i] <= x[i] <= hi[i]

    每轮迭代断言:
        f_new <= f_cur + c1*alpha*dphi0 < f_cur  （充分下降 => 严格单调下降）
    """
    line_search = LINE_SEARCHES[method]
    x = list(x0)
    n = len(x)

    if bounds is not None:
        lo, hi = bounds
        x = [min(max(xi, lo[i]), hi[i]) for i, xi in enumerate(x)]
        alpha_cap = 1.0     # 投影方向的天然步长上限
    else:
        alpha_cap = float("inf")

    feval_count = [0]

    def f_counted(z):
        feval_count[0] += 1
        return f(z)

    fx = f_counted(x)
    g = grad(x)
    f_history = [fx]
    alpha_init = min(alpha0, alpha_cap)
    message = "达到 max_iter"
    converged = False
    n_iter = 0

    for it in range(1, max_iter + 1):
        n_iter = it
        # 下降方向：无约束取 -g；有约束取单位投影步方向（仍为下降方向）
        if bounds is not None:
            d = [min(max(x[i] - g[i], lo[i]), hi[i]) - x[i] for i in range(n)]
            if _norm(d) <= tol:
                converged, message = True, "投影梯度范数收敛"
                break
        else:
            if _norm(g) <= tol:
                converged, message = True, "梯度范数收敛"
                break
            d = [-gi for gi in g]

        dphi0 = _dot(g, d)
        if dphi0 >= 0:      # 数值异常兜底：回退最速下降方向
            d = [-gi for gi in g]
            dphi0 = -_dot(g, g)
            if dphi0 == 0.0:
                converged, message = True, "梯度为零"
                break

        def phi(alpha, _x=x, _d=d):
            return f_counted([_x[i] + alpha * _d[i] for i in range(n)])

        res = line_search(phi, fx, dphi0, min(alpha_init, alpha_cap), c1=c1)
        if not res.converged:
            message = f"线搜索失败: {res.message}"
            break

        # --- 充分下降（Armijo）断言：线搜索契约的直接验证 ---
        assert res.f_new <= fx + c1 * res.alpha * dphi0 + 1e-12 * max(1.0, abs(fx)), \
            f"Armijo 充分下降被破坏: iter={it}"
        # --- 目标值严格单调下降断言 ---
        assert res.f_new < fx + 1e-12 * max(1.0, abs(fx)), \
            f"目标值未单调下降: iter={it}, {res.f_new!r} !< {fx!r}"

        x = [x[i] + res.alpha * d[i] for i in range(n)]
        fx = res.f_new
        g = grad(x)
        f_history.append(fx)
        alpha_init = min(res.alpha * alpha_growth, alpha_cap)

    assert_monotone_decreasing(f_history)
    return OptimizeResult(x, fx, n_iter, feval_count[0],
                          converged, message, f_history)
