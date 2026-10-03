"""数值实验工具：谱验证、误差增长、临界步长扫描、精度/耗时对比、边界用例。"""

import math
import random
import time

from .schemes import evolve
from .von_neumann import amplification, critical_dt, max_growth


def _linspace(x0, x1, n):
    """内部点坐标 x_j = x0 + (j+0.5)*dx? 不用——与 schemes 网格一致: x0 + (j+1)*dx。"""
    dx = (x1 - x0) / (n + 1)
    return [x0 + (j + 1) * dx for j in range(n)], dx


def spectral_check(scheme, a, nu, dx, dt, k_wave=1, n=200, steps=5, x0=0.0, x1=1.0):
    """单谐波验证：实测放大因子 vs 理论 |G|。

    格式均为实系数线性算子，故分别推进实部/虚部再合成复数解。
    返回 dict(measured, theory, theta)。
    """
    xs, _ = _linspace(x0, x1, n)
    k = 2.0 * math.pi * k_wave / (x1 - x0)
    re0 = [math.cos(k * x) for x in xs]
    im0 = [math.sin(k * x) for x in xs]
    re = evolve(scheme, re0, dx, dt, a, nu, steps)
    im = evolve(scheme, im0, dx, dt, a, nu, steps)
    # 取中间点拟合振幅（避开边界截断误差）
    j = n // 2
    amp_end = math.hypot(re[j], im[j])
    amp_start = math.hypot(re0[j], im0[j])
    measured = (amp_end / amp_start) ** (1.0 / steps)
    c = a * dt / dx
    r = nu * dt / (dx * dx)
    theta = k * dx
    theory = abs(amplification(scheme, theta, c, r))
    return {"measured": measured, "theory": theory, "theta": theta,
            "c": c, "r": r, "steps": steps}


def growth_experiment(scheme, a, nu, dx, dt, n=200, steps=30, seed=0, ic="worst",
                      bc="periodic", bc_left=0.0, bc_right=0.0):
    """误差增长实验。

    ic="worst": 以理论最危险模态（argmax_theta |G|，吸附到周期网格允许波数）为初值，
                逐步范数比从第一步起即贴近 max|G|，临界点判定最清晰；
    ic="random": 宽带随机扰动，模拟一般误差的增长。

    返回 dict(growth, per_step_max, per_step_last, maxG_theory, theta, stable_flag)。
    稳定判据: 任意一步范数比 > 1 + 1e-6 即判失稳（稳定格式算子范数 <= 1）。
    """
    c = a * dt / dx
    r = nu * dt / (dx * dx)
    gmax, theta = max_growth(scheme, c, r)

    if ic == "worst":
        m = max(1, int(round(theta * (n + 1) / (2.0 * math.pi))))
        theta_g = 2.0 * math.pi * m / (n + 1)
        u0 = [math.sin(theta_g * (j + 1)) for j in range(n)]
    else:
        rng = random.Random(seed)
        u0 = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    norm0 = math.sqrt(sum(v * v for v in u0) / n)

    from .schemes import step as _step
    g = [0.0] + list(u0) + [0.0]
    t = 0.0
    prev = norm0
    per_step_max = 0.0
    per_step_last = 0.0
    for _ in range(steps):
        _step(scheme, g, dx, dt, a, nu, t, bc, bc_left, bc_right)
        t += dt
        cur = math.sqrt(sum(v * v for v in g[1:n + 1]) / n)
        if prev > 0:
            per_step_last = cur / prev
            per_step_max = max(per_step_max, per_step_last)
        prev = cur
    norm_end = prev
    growth = norm_end / norm0 if norm0 > 0 else float("nan")
    return {"growth": growth, "per_step_max": per_step_max,
            "per_step_last": per_step_last, "maxG_theory": gmax, "theta": theta,
            "norm0": norm0, "norm_end": norm_end,
            "stable_flag": per_step_max <= 1.0 + 1e-6}


def critical_sweep(scheme, a, nu, dx, factors=(0.9, 0.95, 0.99, 1.0, 1.01, 1.05, 1.1),
                   n=200, steps=30, bc="periodic", bc_left=0.0, bc_right=0.0):
    """围绕理论临界步长扫描，返回每档的增长数据。"""
    dt_crit, cond = critical_dt(scheme, dx, a, nu)
    rows = []
    for f in factors:
        dt = f * dt_crit
        res = growth_experiment(scheme, a, nu, dx, dt, n=n, steps=steps,
                                bc=bc, bc_left=bc_left, bc_right=bc_right)
        c = a * dt / dx
        r = nu * dt / (dx * dx)
        rows.append({"factor": f, "dt": dt, "c": c, "r": r,
                     "maxG_theory": res["maxG_theory"], "theta": res["theta"],
                     "growth": res["growth"],
                     "per_step_last": res["per_step_last"],
                     "per_step_max": res["per_step_max"],
                     "stable": res["stable_flag"]})
    return {"scheme": scheme, "dt_crit": dt_crit, "condition": cond, "rows": rows}


def accuracy_timing(scheme, exact, u0_fn, a, nu, dx, dt, t_end,
                    bc="periodic", bc_left=0.0, bc_right=0.0, x0=0.0, x1=1.0):
    """精度 + 耗时对比。exact(x, t) 为精确解。返回 dict。"""
    n = int(round((x1 - x0) / dx)) - 1
    xs, _ = _linspace(x0, x1, n)
    u0 = [u0_fn(x) for x in xs]
    nsteps = int(round(t_end / dt))
    t_start = time.perf_counter()
    u = evolve(scheme, u0, dx, dt, a, nu, nsteps,
               bc=bc, bc_left=bc_left, bc_right=bc_right)
    wall = time.perf_counter() - t_start
    t_final = nsteps * dt
    err_l2 = math.sqrt(sum((u[j] - exact(xs[j], t_final)) ** 2 for j in range(n)) / n)
    err_linf = max(abs(u[j] - exact(xs[j], t_final)) for j in range(n))
    return {"scheme": scheme, "dt": dt, "nsteps": nsteps,
            "err_l2": err_l2, "err_linf": err_linf, "wall_s": wall}
