"""数值实验：临界步长验证、误差增长、显隐式对比、边界条件。

运行: python3 -m stability_analysis.experiments
"""

import math
import time

from . import von_neumann as vn
from . import solvers as sv
from . import exact


def _fmt(x):
    if x != x:  # NaN
        return 'nan'
    ax = abs(x)
    if ax >= 1e5 or (ax != 0.0 and ax < 1e-3):
        return '%.3e' % x
    return '%.6f' % x


# ---------------------------------------------------------------- 1. 放大因子扫描

def experiment_amplification_scan():
    """验证解析临界步长: dt_c 附近 max|G| 的跨越。"""
    print('=' * 72)
    print('实验 1: von Neumann 放大因子扫描 —— 验证解析临界步长')
    print('=' * 72)
    a, nu, dx = 1.0, 0.01, 0.01
    cases = [
        ('迎风对流 (c<=1)',
         vn.critical_dt_upwind(a, dx),
         lambda dt: vn.max_amplification(
             lambda th: vn.g_upwind_advection(th, a * dt / dx))),
        ('FTCS 扩散 (r<=1/2)',
         vn.critical_dt_diffusion(nu, dx),
         lambda dt: vn.max_amplification(
             lambda th: vn.g_ftcs_diffusion(th, nu * dt / dx ** 2))),
        ('迎风对流+中心扩散 (c+2r<=1)',
         vn.critical_dt_upwind_adv_diff(a, nu, dx),
         lambda dt: vn.max_amplification(
             lambda th: vn.g_upwind_adv_diff(th, a * dt / dx, nu * dt / dx ** 2))),
        ('中心对流+中心扩散 (r<=1/2, c^2<=2r)',
         vn.critical_dt_central_adv_diff(a, nu, dx),
         lambda dt: vn.max_amplification(
             lambda th: vn.g_central_adv_diff(th, a * dt / dx, nu * dt / dx ** 2))),
    ]
    print('参数: a=%g, nu=%g, dx=%g' % (a, nu, dx))
    print('%-34s %10s %12s %12s %8s' %
          ('格式', 'dt_c', 'max|G|(0.99c)', 'max|G|(1.01c)', '判定'))
    for name, dtc, scan in cases:
        below = scan(0.99 * dtc)
        above = scan(1.01 * dtc)
        ok = below <= 1.0 + 1e-12 and above > 1.0
        print('%-34s %10.6f %12.8f %12.8f %8s' %
              (name, dtc, below, above, '符合' if ok else '异常'))

    # FTCS 纯对流: 任意小 dt 都不稳定
    print()
    print('FTCS 纯对流（中心差分）: 理论恒不稳定，数值验证:')
    for c in (0.01, 0.1, 0.5, 0.99):
        g = vn.max_amplification(lambda th: vn.g_ftcs_advection(th, c))
        print('  c=%.2f  max|G|=%.8f  (>1, 不稳定)' % (c, g))
    # 隐式: 大 dt 仍稳定
    print()
    print('隐式（向后Euler, 迎风+中心扩散）: 无条件稳定，数值验证:')
    for mult in (1.0, 10.0, 100.0):
        dt = mult * vn.critical_dt_upwind_adv_diff(a, nu, dx)
        g = vn.max_amplification(
            lambda th: vn.g_implicit_upwind_adv_diff(
                th, a * dt / dx, nu * dt / dx ** 2))
        print('  dt=%8.4f (%5.1f x dt_c_explicit)  max|G|=%.8f (<=1)'
              % (dt, mult, g))
    print()


# ---------------------------------------------------------------- 2. 误差增长

def _growth_case(name, u0, exact_fn, dt_c, nsteps, adv, a, nu, dx,
                 ratios=(0.9, 1.1)):
    print('--- %s ---' % name)
    print('dt_c = %.6f' % dt_c)
    print('%-8s' % 'dt/dt_c' + ''.join('%14s' % ('t/T=%.2f' % f)
                                       for f in (0.25, 0.5, 0.75, 1.0)))
    for ratio in ratios:
        dt = ratio * dt_c
        steps = max(1, int(round(nsteps / ratio)))
        marks = {max(1, int(steps * f)) for f in (0.25, 0.5, 0.75, 1.0)}
        u, records = sv.explicit_solve(u0, dx, dt, steps, a, nu,
                                       bc='periodic', adv=adv,
                                       record_every=1)
        rec = dict((s, v) for s, v in records)
        errs = []
        for s in sorted(marks):
            v = rec[s]
            ue = [exact_fn(x, s * dt) for x in
                  (i * dx for i in range(len(v)))]
            errs.append(sv.l2_error(v, ue, dx))
        print('%-8.2f' % ratio + ''.join('%14s' % _fmt(e) for e in errs))
    print()


EPS = 1e-6  # 注入的高频扰动幅度


def _perturbation_growth(name, mode_g, theta_star, dtc, nsteps, adv,
                         a, nu, dx, ratios):
    """注入单一傅里叶模态小扰动, 测其幅度放大率（与 |G(theta*)|^n 对比）。"""
    print('--- %s ---' % name)
    N = int(round(1.0 / dx))
    pert0 = [EPS * math.cos(theta_star * i) for i in range(N)]
    print('  扰动模态 theta*=%.4f, 初幅 %.0e' % (theta_star, EPS))
    print('  %-8s %6s %16s %16s %10s' %
          ('dt/dt_c', '步数', '扰动终幅', '实测放大率', '理论|G|^n'))
    for ratio in ratios:
        dt = ratio * dtc
        c = a * dt / dx
        r = nu * dt / dx ** 2
        u, _ = sv.explicit_solve(pert0, dx, dt, nsteps, a, nu,
                                 bc='periodic', adv=adv)
        final = sv.l2_norm(u, dx)
        init = sv.l2_norm(pert0, dx)
        measured = final / init
        predicted = abs(mode_g(theta_star, c, r)) ** nsteps
        print('  %-8.2f %6d %16s %16s %16s' %
              (ratio, nsteps, _fmt(final), _fmt(measured), _fmt(predicted)))
    print()


def experiment_error_growth():
    """临界步长附近: (A) 物理解 L2 误差; (B) 高频扰动放大率。"""
    print('=' * 72)
    print('实验 2: 临界步长附近的误差与扰动增长（周期边界）')
    print('=' * 72)
    L, N = 1.0, 100
    dx = L / N

    # A. 物理解误差（0.9 与 1.1 临界步长, 固定物理时长 T = 100*dt_c）
    print('A. 物理解 L2 误差（与精确解对比）')
    print()
    a, nu = 1.0, 0.0
    u0 = [exact.advected_sine(i * dx, 0.0, a, L) for i in range(N)]
    dtc = vn.critical_dt_upwind(a, dx)
    _growth_case('2a 纯对流 a=1, 迎风显式 (T=1.0)', u0,
                 lambda x, t: exact.advected_sine(x, t, a, L),
                 dtc, 100, 'upwind', a, nu, dx)

    a, nu = 0.0, 0.01
    u0 = [exact.decaying_sine(i * dx, 0.0, nu, L) for i in range(N)]
    dtc = vn.critical_dt_diffusion(nu, dx)
    _growth_case('2b 纯扩散 nu=0.01, FTCS 显式 (T=0.5)', u0,
                 lambda x, t: exact.decaying_sine(x, t, nu, L),
                 dtc, 100, 'upwind', a, nu, dx)

    a, nu = 1.0, 0.01
    u0 = [exact.adv_diff_sine(i * dx, 0.0, a, nu, L) for i in range(N)]
    dtc = vn.critical_dt_upwind_adv_diff(a, nu, dx)
    _growth_case('2c 对流扩散 a=1, nu=0.01, 迎风+中心扩散 (T~0.33)', u0,
                 lambda x, t: exact.adv_diff_sine(x, t, a, nu, L),
                 dtc, 100, 'upwind', a, nu, dx)

    # B. 高频扰动放大率（直接验证 von Neumann 临界条件）
    print('B. 高频小扰动放大率（100 步; >1 即失稳, 实测应与理论 |G|^n 吻合）')
    print()
    a, nu = 1.0, 0.0
    dtc = vn.critical_dt_upwind(a, dx)
    _perturbation_growth(
        '2a 纯对流迎风, 最危险模态 theta*=pi',
        lambda th, c, r: vn.g_upwind_advection(th, c),
        math.pi, dtc, 100, 'upwind', a, nu, dx, (0.5, 0.9, 0.99, 1.01, 1.1))

    a, nu = 0.0, 0.01
    dtc = vn.critical_dt_diffusion(nu, dx)
    _perturbation_growth(
        '2b 纯扩散 FTCS, 最危险模态 theta*=pi',
        lambda th, c, r: vn.g_ftcs_diffusion(th, r),
        math.pi, dtc, 100, 'upwind', a, nu, dx, (0.5, 0.9, 0.99, 1.01, 1.1))

    a, nu = 1.0, 0.01
    dtc = vn.critical_dt_upwind_adv_diff(a, nu, dx)
    _perturbation_growth(
        '2c 对流扩散迎风+中心, 最危险模态 theta*=pi',
        lambda th, c, r: vn.g_upwind_adv_diff(th, c, r),
        math.pi, dtc, 100, 'upwind', a, nu, dx, (0.5, 0.9, 0.99, 1.01, 1.1))

    # FTCS 纯对流: 最危险模态 theta*=pi/2, 任意 c>0 都放大
    a, nu = 1.0, 0.0
    dtc = dx / a  # 仅作步长参照 (此格式无稳定区)
    _perturbation_growth(
        '2d FTCS 纯对流（理论恒不稳定）, 最危险模态 theta*=pi/2',
        lambda th, c, r: vn.g_ftcs_advection(th, c),
        math.pi / 2.0, dtc, 100, 'central', a, nu, dx, (0.1, 0.5, 0.9))
    print()


# ---------------------------------------------------------------- 3. 显式 vs 隐式

def experiment_implicit_vs_explicit():
    """纯扩散问题: 显式(近临界dt) vs 隐式(多种dt) 的耗时与精度。"""
    print('=' * 72)
    print('实验 3: 显式 vs 隐式 —— 耗时与精度对比（纯扩散, nu=0.01, T=0.5）')
    print('=' * 72)
    L, N, nu, T = 1.0, 200, 0.01, 0.5
    dx = L / N
    u0 = [exact.decaying_sine(i * dx, 0.0, nu, L) for i in range(N)]
    ue = [exact.decaying_sine(i * dx, T, nu, L) for i in range(N)]
    dt_c = vn.critical_dt_diffusion(nu, dx)

    print('%-22s %12s %10s %14s %12s' %
          ('方法', 'dt', '步数', 'L2 误差', '耗时(ms)'))
    rows = []
    for mult, label in ((0.25, '显式 FTCS (0.25*dt_c)'),
                        (0.95, '显式 FTCS (0.95*dt_c)')):
        dt = mult * dt_c
        steps = int(round(T / dt))
        dt = T / steps
        t0 = time.perf_counter()
        u, _ = sv.explicit_solve(u0, dx, dt, steps, 0.0, nu, bc='periodic')
        elap = (time.perf_counter() - t0) * 1e3
        rows.append((label, dt, steps, sv.l2_error(u, ue, dx), elap))

    for mult in (0.5, 10.0, 50.0):
        dt = mult * dt_c
        steps = max(1, int(round(T / dt)))
        dt = T / steps
        t0 = time.perf_counter()
        u, _ = sv.implicit_solve(u0, dx, dt, steps, 0.0, nu, bc='periodic')
        elap = (time.perf_counter() - t0) * 1e3
        rows.append(('隐式 BE (%g x dt_c)' % mult, dt, steps,
                     sv.l2_error(u, ue, dx), elap))

    for name, dt, steps, err, elap in rows:
        print('%-22s %12.6f %10d %14s %12.2f' %
              (name, dt, steps, _fmt(err), elap))
    print()
    print('说明: 显式受 dt_c=%.6f 限制必须走小步长; 隐式可用 50 倍步长,'
          ' 精度损失约一个量级但耗时大幅下降。' % dt_c)
    print()


# ---------------------------------------------------------------- 4. 边界条件

def experiment_boundary_conditions():
    """同一格式在不同边界条件下的行为。"""
    print('=' * 72)
    print('实验 4: 边界条件用例（迎风显式, a=1）')
    print('=' * 72)
    L, N, a = 1.0, 400, 1.0
    dx = L / N
    dt = 0.5 * dx / a  # c=0.5, 格式内部稳定

    # 4a Dirichlet 入流: u(0,t)=sin(2 pi t), 精确解 u(x,t)=g(t-x/a)
    T = 0.6
    steps = int(round(T / dt))
    g = lambda t: math.sin(2.0 * math.pi * t)
    u0 = [0.0] * (N + 1)
    u, _ = sv.explicit_solve(u0, dx, dt, steps, a, 0.0,
                             bc='dirichlet', g_left=g, g_right=lambda t: 0.0)
    # 精确解只在波已到达区域 x < a*T 有效（出口附近受右边界影响）
    n_valid = int(0.9 * a * T / dx)
    ue = [g(T - i * dx / a) for i in range(n_valid)]
    err = sv.l2_error(u[:n_valid], ue, dx)
    print('4a Dirichlet 入流 u(0,t)=sin(2*pi t), T=%.1f:' % T)
    print('   波前区 L2 误差 = %s, max|u| = %.4f (有界, 稳定)'
          % (_fmt(err), max(abs(v) for v in u)))

    # 4b 出流(零梯度): 初始正弦凸包向右移出
    T = 0.8
    steps = int(round(T / dt))
    u0 = [math.sin(math.pi * i * dx / L) for i in range(N + 1)]
    u, _ = sv.explicit_solve(u0, dx, dt, steps, a, 0.0,
                             bc='outflow', g_left=lambda t: 0.0)
    ue = [math.sin(math.pi * (i * dx - a * T) / L) if i * dx > a * T else 0.0
          for i in range(N + 1)]
    err = sv.l2_error(u, ue, dx)
    print('4b 出流零梯度边界, 凸包右移 T=%.1f:' % T)
    print('   L2 误差 = %s, max|u| = %.4f (有界, 稳定)'
          % (_fmt(err), max(abs(v) for v in u)))

    # 4c Neumann 零通量: 纯扩散 cos(pi x) 衰减
    nu = 0.05
    dt_d = 0.4 * vn.critical_dt_diffusion(nu, dx)
    T = 0.5
    steps = int(round(T / dt_d))
    u0 = [1.0 + 0.5 * math.cos(math.pi * i * dx / L)
          for i in range(N + 1)]
    u, _ = sv.explicit_solve(u0, dx, dt_d, steps, 0.0, nu, bc='neumann')
    ue = [1.0 + 0.5 * math.cos(math.pi * i * dx / L)
          * math.exp(-nu * math.pi ** 2 * T)
          for i in range(N + 1)]
    err = sv.l2_error(u, ue, dx)
    mass0 = dx * sum(u0)
    mass1 = dx * sum(u)
    print('4c Neumann 零梯度(零通量) 纯扩散, T=%.1f:' % T)
    print('   L2 误差 = %s, 总质量 %.6f -> %.6f (近似守恒)'
          % (_fmt(err), mass0, mass1))

    # 4d 非周期边界下显式格式仍受同样的临界步长约束
    a2, nu2 = 1.0, 0.01
    dtc = vn.critical_dt_upwind_adv_diff(a2, nu2, dx)
    print('4d Dirichlet 边界下对流扩散 (dt_c=%.6f):' % dtc)
    u0 = [0.0] * (N + 1)
    for ratio in (0.9, 1.1):
        dt2 = ratio * dtc
        steps2 = 300
        u, _ = sv.explicit_solve(u0, dx, dt2, steps2, a2, nu2,
                                 bc='dirichlet',
                                 g_left=lambda t: 1.0, g_right=lambda t: 0.0)
        mx = max(abs(v) for v in u)
        print('   dt/dt_c=%.1f: 300 步后 max|u| = %s %s'
              % (ratio, _fmt(mx), '(有界)' if mx < 10.0 else '(发散!)'))
    print()


# ---------------------------------------------------------------- main

def main():
    experiment_amplification_scan()
    experiment_error_growth()
    experiment_implicit_vs_explicit()
    experiment_boundary_conditions()


if __name__ == '__main__':
    main()
