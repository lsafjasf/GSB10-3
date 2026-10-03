"""线搜索与步长选择库（仅 Python 标准库）。

实现两种一维线搜索：
1. backtracking_line_search   —— 回溯法（固定比例收缩）
2. interpolation_line_search  —— 插值法（二次/三次插值选取试探步长）

两种方法的终止条件相同，均为 Armijo 充分下降条件：

    f(x_new) <= f(x) + c1 * <grad f(x), x_new - x>,   0 < c1 < 1

其中 x_new = project(x + alpha * d)。无约束时 project 为恒等映射，
条件退化为经典形式 f(x + a d) <= f(x) + c1 * a * <g, d>。
由于 <g, x_new - x> < 0（d 为下降方向），每轮迭代目标值严格下降。

另提供 gradient_descent：以外层梯度下降 + 上述线搜索组成的求解器，
每一轮都对目标值的单调下降做断言（assert）。
"""

import math

__all__ = [
    "LineSearchError",
    "backtracking_line_search",
    "interpolation_line_search",
    "gradient_descent",
    "assert_monotone_decreasing",
]


class LineSearchError(RuntimeError):
    """线搜索未能在允许范围内找到满足充分下降条件的步长。"""


def _dot(a, b):
    return sum(ai * bi for ai, bi in zip(a, b))


def _try_step(f, x, d, alpha, project):
    """计算 x_new = project(x + alpha*d) 及其目标值。"""
    x_new = [xi + alpha * di for xi, di in zip(x, d)]
    if project is not None:
        x_new = list(project(x_new))
    return x_new, f(x_new)


def _armijo_ok(fx, f_new, g, x, x_new, c1):
    """Armijo 充分下降判定（基于实际位移，兼容投影情形）。"""
    actual = _dot(g, [xn - xi for xn, xi in zip(x_new, x)])
    return f_new <= fx + c1 * actual


def backtracking_line_search(f, grad, x, d, alpha0=1.0, rho=0.5, c1=1e-4,
                             alpha_min=1e-16, max_iter=200, project=None):
    """回溯线搜索。

    终止条件（满足其一即返回）：
      1. 成功：f(project(x + a d)) <= f(x) + c1 * <g, project(x + a d) - x>
         （Armijo 充分下降），返回 (alpha, x_new, f_new)；
      2. 失败：alpha 收缩到 alpha_min 以下或超过 max_iter 次仍未满足，
         抛出 LineSearchError。

    步长更新规则：alpha <- rho * alpha，0 < rho < 1。
    """
    fx = f(x)
    g = grad(x)
    if _dot(g, d) >= 0.0:
        raise LineSearchError("d 不是下降方向（<g, d> >= 0）")
    alpha = alpha0
    for _ in range(max_iter):
        x_new, f_new = _try_step(f, x, d, alpha, project)
        if _armijo_ok(fx, f_new, g, x, x_new, c1):
            return alpha, x_new, f_new
        alpha *= rho
        if alpha < alpha_min:
            break
    raise LineSearchError(
        "回溯线搜索失败：步长已收缩至 %.3e 以下仍未满足充分下降" % alpha_min)


def _quadratic_step(alpha, phi_alpha, phi0, der0):
    """由 phi(0), phi'(0), phi(alpha) 拟合二次模型并取其极小点。"""
    denom = 2.0 * (phi_alpha - phi0 - der0 * alpha)
    if denom <= 0.0:  # 数值退化，退化为减半
        return 0.5 * alpha
    return -der0 * alpha * alpha / denom


def _cubic_step(u, phi_u, v, phi_v, phi0, der0):
    """由 phi(0), phi'(0), phi(u), phi(v) 拟合三次模型并取其极小点。

    拟合 p(t) = phi0 + der0*t + c2*t^2 + c3*t^3，返回 p 在 (0, v) 内
    的极小点；若退化（c3 为 0 或判别式为负）则返回 None 由调用方回退。
    """
    r_u = phi_u - phi0 - der0 * u
    r_v = phi_v - phi0 - der0 * v
    det = u * u * v * v * (v - u)
    if det == 0.0:
        return None
    c3 = (r_v * u * u - r_u * v * v) / det
    if c3 == 0.0:
        return None
    c2 = (r_u - c3 * u ** 3) / (u * u)
    disc = c2 * c2 - 3.0 * c3 * der0
    if disc < 0.0:
        return None
    t = (-c2 + math.sqrt(disc)) / (3.0 * c3)
    # 健全性检查：极小点必须落在已知区间内部，否则视为退化并回退
    if not (0.0 < t < max(u, v)):
        return None
    return t


def interpolation_line_search(f, grad, x, d, alpha0=1.0, c1=1e-4,
                              alpha_min=1e-16, max_iter=60, project=None):
    """插值线搜索（二次 + 三次插值，Nocedal & Wright 3.4 节风格）。

    终止条件（满足其一即返回）：
      1. 成功：Armijo 充分下降条件（与回溯法完全相同的形式）；
      2. 失败：步长区间收缩至 alpha_min 以下或超过 max_iter 次迭代，
         抛出 LineSearchError。

    步长更新规则：
      - 首次失败：用 phi(0), phi'(0), phi(a) 作二次插值取极小点；
      - 之后失败：用最近两个试探点作三次插值取极小点，退化时回退二次；
      - 安全保护：新步长被钳制在 [0.1*a, 0.9*a]，保证区间持续收缩。
    """
    fx = f(x)
    g = grad(x)
    der0 = _dot(g, d)
    if der0 >= 0.0:
        raise LineSearchError("d 不是下降方向（<g, d> >= 0）")

    alpha = alpha0
    alpha_prev, phi_prev = None, None
    for _ in range(max_iter):
        x_new, f_new = _try_step(f, x, d, alpha, project)
        if _armijo_ok(fx, f_new, g, x, x_new, c1):
            return alpha, x_new, f_new

        if alpha_prev is None:
            alpha_next = _quadratic_step(alpha, f_new, fx, der0)
        else:
            alpha_next = _cubic_step(alpha_prev, phi_prev, alpha, f_new,
                                     fx, der0)
            if alpha_next is None:
                alpha_next = _quadratic_step(alpha, f_new, fx, der0)

        alpha_prev, phi_prev = alpha, f_new
        lo, hi = 0.1 * alpha, 0.9 * alpha
        alpha = min(max(alpha_next, lo), hi)
        if alpha < alpha_min:
            break
    raise LineSearchError(
        "插值线搜索失败：步长已收缩至 %.3e 以下仍未满足充分下降" % alpha_min)


def assert_monotone_decreasing(history, tol=1e-12):
    """断言目标值序列单调不增（允许浮点级误差）。"""
    for i in range(1, len(history)):
        limit = history[i - 1] + tol * max(1.0, abs(history[i - 1]))
        assert history[i] <= limit, (
            "单调下降断言失败：f[%d]=%.17g > f[%d]=%.17g"
            % (i, history[i], i - 1, history[i - 1]))


def gradient_descent(f, grad, x0, line_search, tol=1e-8, max_iter=10000,
                     alpha0=1.0, project=None, **ls_kwargs):
    """梯度下降 + 线搜索，每轮断言目标值单调下降。

    返回 (x, f(x), 迭代轮数, 目标值历史)。收敛判据为 ||grad f||_2 <= tol。
    """
    x = list(x0)
    fx = f(x)
    history = [fx]
    iters = 0
    for k in range(max_iter):
        g = grad(x)
        if project is not None:
            # 约束问题的驻点判据：||x - project(x - g)|| <= tol
            px = list(project([xi - gi for xi, gi in zip(x, g)]))
            stat = math.sqrt(sum((xi - pi) ** 2
                                 for xi, pi in zip(x, px)))
            if stat <= tol:
                break
        elif math.sqrt(_dot(g, g)) <= tol:
            break
        d = [-gi for gi in g]
        _, x, f_new = line_search(f, grad, x, d, alpha0=alpha0,
                                  project=project, **ls_kwargs)
        # 充分下降 => f_new < fx（数学上严格成立），此处允许浮点级误差
        assert f_new <= fx + 1e-12 * max(1.0, abs(fx)), (
            "第 %d 轮单调下降断言失败：%.17g -> %.17g" % (k, fx, f_new))
        fx = f_new
        history.append(fx)
        iters = k + 1
    assert_monotone_decreasing(history)
    return x, fx, iters, history
