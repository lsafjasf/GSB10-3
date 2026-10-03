"""数值实验主程序：python3 demo.py
输出打印到终端，同时写入 results/report.md。
"""

import math
import os
import time

from fdstab.von_neumann import critical_dt, max_growth
from fdstab.experiments import (
    spectral_check, critical_sweep, accuracy_timing, growth_experiment,
)

A, NU = 1.0, 0.01
DX = 0.005
LINES = []


def out(s=""):
    print(s)
    LINES.append(s)


def fmt_row(cols, widths):
    return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cols, widths)) + " |"


def section_spectral():
    out("## 1. 放大因子：理论 vs 实测（单谐波谱验证）")
    out()
    out("初值取单谐波 exp(i k x)，推进 5 步后实测每步振幅放大倍数，与 Von Neumann 理论 |G| 对比。")
    out()
    hdr = ["格式", "c", "r", "theta", "理论|G|", "实测|G|", "相对偏差"]
    w = [16, 8, 8, 8, 10, 10, 10]
    out(fmt_row(hdr, w))
    out(fmt_row(["---"] * 7, w))
    cases = [
        ("ftcs", A, NU, 0.9 * critical_dt("ftcs", DX, A, NU)[0]),
        ("upwind", A, NU, 0.9 * critical_dt("upwind", DX, A, NU)[0]),
        ("lax_friedrichs", A, 0.0, 0.9 * DX / A),
        ("lax_wendroff", A, 0.0, 0.9 * DX / A),
        ("btcs", A, NU, 5.0 * DX / A),
        ("crank_nicolson", A, NU, 5.0 * DX / A),
    ]
    for scheme, a, nu, dt in cases:
        res = spectral_check(scheme, a, nu, DX, dt, k_wave=1, n=200, steps=5)
        rel = abs(res["measured"] - res["theory"]) / max(res["theory"], 1e-30)
        out(fmt_row([scheme, "%.3f" % res["c"], "%.4f" % res["r"],
                     "%.4f" % res["theta"], "%.6f" % res["theory"],
                     "%.6f" % res["measured"], "%.2e" % rel], w))
    out()


def section_critical():
    out("## 2. 临界步长扫描：略小稳定、略大失稳")
    out()
    out("初值取理论最危险模态，推进 30 步。`理论max|G|` 为 Von Neumann 扫描结果，"
        "`实测每步增长` 为最后一步 ||u^{n+1}||/||u^n||，`总增长` 为 30 步范数比。")
    scenarios = [
        ("纯扩散 FTCS", "ftcs", 0.0, NU),
        ("纯对流 迎风", "upwind", A, 0.0),
        ("纯对流 Lax-Friedrichs", "lax_friedrichs", A, 0.0),
        ("纯对流 Lax-Wendroff", "lax_wendroff", A, 0.0),
        ("对流扩散 FTCS", "ftcs", A, NU),
        ("对流扩散 迎风", "upwind", A, NU),
    ]
    for name, scheme, a, nu in scenarios:
        sw = critical_sweep(scheme, a, nu, DX,
                            factors=(0.95, 0.99, 1.0, 1.01, 1.05),
                            n=200, steps=30)
        out("### %s" % name)
        out()
        out("- 稳定条件: `%s`" % sw["condition"])
        out("- 理论临界步长 dt_crit = %.6g" % sw["dt_crit"])
        out()
        hdr = ["dt/dt_crit", "c", "r", "理论max|G|", "实测每步增长", "总增长(30步)", "判定"]
        w = [10, 8, 8, 11, 13, 13, 6]
        out(fmt_row(hdr, w))
        out(fmt_row(["---"] * 7, w))
        for row in sw["rows"]:
            out(fmt_row(["%.2f" % row["factor"], "%.4f" % row["c"], "%.4f" % row["r"],
                         "%.6f" % row["maxG_theory"], "%.6f" % row["per_step_last"],
                         "%.4g" % row["growth"],
                         "稳定" if row["stable"] else "失稳"], w))
        out()
    out("### 纯对流 FTCS（恒不稳定验证）")
    out()
    dt_crit, cond = critical_dt("ftcs", DX, A, 0.0)
    out("- 理论: %s" % cond)
    for f in (0.1, 0.5, 0.9):
        dt = f * DX / A
        res = growth_experiment("ftcs", A, 0.0, DX, dt, n=200, steps=30)
        out("- c=%.2f: 理论max|G|=%.6f, 实测每步增长=%.6f, 30步总增长=%.4g -> %s"
            % (f, res["maxG_theory"], res["per_step_last"], res["growth"],
               "失稳" if not res["stable_flag"] else "稳定"))
    out()


def section_accuracy_timing():
    out("## 3. 隐式 vs 显式：精度与耗时对比")
    out()
    out("问题: u_t + u_x = 0.01 u_xx, 周期边界, u0=sin(2 pi x), t_end=0.5, N=199 (dx=0.005)。")
    out("精确解: u = exp(-nu (2pi)^2 t) sin(2 pi (x - t))。")
    out("显式取 dt=0.9*dt_crit；隐式分别取相同 dt 与 10 倍 dt（展示无条件稳定带来的步长优势）。")
    out()
    t_end = 0.5
    nu, a = NU, A
    k = 2.0 * math.pi

    def u0_fn(x):
        return math.sin(k * x)

    def exact(x, t):
        return math.exp(-nu * k * k * t) * math.sin(k * (x - a * t))

    dt_exp = 0.9 * critical_dt("ftcs", DX, a, nu)[0]
    runs = [
        ("ftcs (显式)", "ftcs", dt_exp),
        ("upwind (显式)", "upwind", 0.9 * critical_dt("upwind", DX, a, nu)[0]),
        ("btcs (隐式, 同dt)", "btcs", dt_exp),
        ("btcs (隐式, 10x dt)", "btcs", 10.0 * dt_exp),
        ("cn (隐式, 同dt)", "crank_nicolson", dt_exp),
        ("cn (隐式, 10x dt)", "crank_nicolson", 10.0 * dt_exp),
    ]
    hdr = ["格式", "dt", "步数", "L2误差", "Linf误差", "耗时(s)"]
    w = [20, 10, 7, 11, 11, 9]
    out(fmt_row(hdr, w))
    out(fmt_row(["---"] * 6, w))
    for name, scheme, dt in runs:
        res = accuracy_timing(scheme, exact, u0_fn, a, nu, DX, dt, t_end)
        out(fmt_row([name, "%.2e" % res["dt"], res["nsteps"],
                     "%.3e" % res["err_l2"], "%.3e" % res["err_linf"],
                     "%.4f" % res["wall_s"]], w))
    out()
    out("结论: 隐式格式在 10 倍步长下仍稳定；CN 保持二阶时间精度，误差最小；"
        "BTCS 大步长时时间截断误差主导。纯 Python 下单步隐式比显式贵（解三对角），"
        "但允许步长大一个量级以上时总耗时占优。")
    out()


def section_boundaries():
    out("## 4. 边界条件用例")
    out()
    nu = 0.05
    n = 199
    dx = 1.0 / (n + 1)
    t_end = 0.2

    out("### 4.1 Dirichlet: u(0)=u(1)=0, 纯扩散, u0=sin(pi x)")
    out()
    out("精确解 exp(-nu pi^2 t) sin(pi x)。显式 FTCS 取 r=0.4（临界 r=0.5）。")
    dt = 0.4 * dx * dx / nu
    nsteps = int(round(t_end / dt))
    xs = [(j + 1) * dx for j in range(n)]
    from fdstab.schemes import evolve
    u0 = [math.sin(math.pi * x) for x in xs]
    u = evolve("ftcs", u0, dx, dt, 0.0, nu, nsteps,
               bc="dirichlet", bc_left=0.0, bc_right=0.0)
    t = nsteps * dt
    err = max(abs(u[j] - math.exp(-nu * math.pi ** 2 * t) * math.sin(math.pi * xs[j]))
              for j in range(n))
    out("- FTCS r=0.4: Linf 误差 = %.3e（稳定且精确）" % err)
    dt_bad = 0.6 * dx * dx / nu
    res = growth_experiment("ftcs", 0.0, nu, dx, dt_bad, n=n, steps=40,
                            bc="dirichlet", bc_left=0.0, bc_right=0.0)
    out("- FTCS r=0.6（超临界）: 40 步总增长 = %.4g -> %s（边界不改变内部 Von Neumann 判据）"
        % (res["growth"], "稳定" if res["stable_flag"] else "失稳"))
    out()

    out("### 4.2 Neumann: u_x=0 两端, 纯扩散, u0=cos(pi x)")
    out()
    out("精确解 exp(-nu pi^2 t) cos(pi x)。隐式 BTCS。")
    u0 = [math.cos(math.pi * x) for x in xs]
    u = evolve("btcs", u0, dx, dt, 0.0, nu, nsteps,
               bc="neumann", bc_left=0.0, bc_right=0.0)
    err = max(abs(u[j] - math.exp(-nu * math.pi ** 2 * t) * math.cos(math.pi * xs[j]))
              for j in range(n))
    out("- BTCS: Linf 误差 = %.3e（边界虚元一阶处理，误差略高于内部二阶）" % err)
    out()

    out("### 4.3 周期边界: 纯对流, u0=sin(2 pi x)")
    out()
    a = 1.0
    dt = 0.8 * dx / a
    nsteps = int(round(t_end / dt))
    u0 = [math.sin(2.0 * math.pi * x) for x in xs]
    u = evolve("upwind", u0, dx, dt, a, 0.0, nsteps, bc="periodic")
    t = nsteps * dt
    err = max(abs(u[j] - math.sin(2.0 * math.pi * (xs[j] - a * t))) for j in range(n))
    out("- 迎风 c=0.8: Linf 误差 = %.3e（一阶格式数值扩散所致，波形相位正确）" % err)
    res = growth_experiment("upwind", a, 0.0, dx, 1.05 * dx / a, n=n, steps=40,
                            bc="periodic")
    out("- 迎风 c=1.05（超 CFL）: 40 步总增长 = %.4g -> %s"
        % (res["growth"], "稳定" if res["stable_flag"] else "失稳"))
    out()

    out("### 4.4 时变 Dirichlet 边界")
    out()
    u0 = [0.0] * n
    u = evolve("btcs", u0, dx, dt, 0.0, nu, nsteps,
               bc="dirichlet", bc_left=lambda tt: math.sin(5.0 * tt), bc_right=0.0)
    out("- u(0,t)=sin(5t), u(1,t)=0, BTCS 推进 %d 步: 解有界, max|u|=%.4f, 全部有限: %s"
        % (nsteps, max(abs(v) for v in u), all(math.isfinite(v) for v in u)))
    out()


def main():
    out("# 对流-扩散方程差分格式稳定性实验报告")
    out()
    out("方程: u_t + a u_x = nu u_xx, a=%g, nu=%g, dx=%g。" % (A, NU, DX))
    out("c = a dt/dx, r = nu dt/dx^2。生成时间: %s。" % time.strftime("%Y-%m-%d %H:%M:%S"))
    out()
    section_spectral()
    section_critical()
    section_accuracy_timing()
    section_boundaries()
    os.makedirs("results", exist_ok=True)
    with open(os.path.join("results", "report.md"), "w") as f:
        f.write("\n".join(LINES) + "\n")
    print("\n报告已写入 results/report.md")


if __name__ == "__main__":
    main()
