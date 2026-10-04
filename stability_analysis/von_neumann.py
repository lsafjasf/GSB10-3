"""von Neumann 稳定性分析：放大因子与临界步长。

记号约定
--------
c = a*dt/dx   (Courant 数, 对流)
r = nu*dt/dx^2 (扩散数)
theta = k*dx  (无量纲波数, 扫描 [0, pi])

每个 g_* 函数返回单步放大因子 G(theta)，稳定性要求 max_theta |G| <= 1。
"""

import cmath
import math


# ---------------------------------------------------------------- 放大因子

def g_ftcs_advection(theta, c):
    """纯对流 FTCS（时间前差 + 空间中心差分）。结论：恒不稳定。"""
    return 1.0 - 1j * c * math.sin(theta)


def g_upwind_advection(theta, c):
    """纯对流一阶迎风（a>0）。稳定条件: 0 <= c <= 1。"""
    return 1.0 - c * (1.0 - cmath.exp(-1j * theta))


def g_ftcs_diffusion(theta, r):
    """纯扩散 FTCS（中心差分）。稳定条件: r <= 1/2。"""
    return 1.0 - 2.0 * r * (1.0 - math.cos(theta))


def g_crank_nicolson_diffusion(theta, r):
    """纯扩散 Crank-Nicolson。无条件稳定。"""
    s = 1.0 - math.cos(theta)
    return (1.0 - r * s) / (1.0 + r * s)


def g_upwind_adv_diff(theta, c, r):
    """对流(迎风) + 扩散(中心) 显式。稳定条件: c + 2r <= 1 (c>=0)。"""
    return (1.0 - c * (1.0 - cmath.exp(-1j * theta))
            - 2.0 * r * (1.0 - math.cos(theta)))


def g_central_adv_diff(theta, c, r):
    """对流(中心) + 扩散(中心) 显式 FTCS。
    稳定条件: r <= 1/2 且 c^2 <= 2r。"""
    return 1.0 - 2.0 * r * (1.0 - math.cos(theta)) - 1j * c * math.sin(theta)


def g_implicit_upwind_adv_diff(theta, c, r):
    """对流(迎风) + 扩散(中心) 向后 Euler 隐式。无条件稳定。"""
    denom = (1.0 + c * (1.0 - cmath.exp(-1j * theta))
             + 2.0 * r * (1.0 - math.cos(theta)))
    return 1.0 / denom


# ---------------------------------------------------------------- 扫描工具

def max_amplification(g, n_theta=2001):
    """在 theta ∈ [0, pi] 上数值扫描 max |G|。

    g 为只接受 theta 的单参数可调用对象（其余参数用闭包固定）。
    """
    worst = 0.0
    for i in range(n_theta):
        theta = math.pi * i / (n_theta - 1)
        mag = abs(g(theta))
        if mag > worst:
            worst = mag
    return worst


# ---------------------------------------------------------------- 临界步长

def critical_dt_upwind(a, dx):
    """迎风显式纯对流: dt_c = dx/|a|  (c <= 1)。"""
    return dx / abs(a)


def critical_dt_diffusion(nu, dx):
    """FTCS 显式纯扩散: dt_c = dx^2/(2*nu)  (r <= 1/2)。"""
    return dx * dx / (2.0 * nu)


def critical_dt_upwind_adv_diff(a, nu, dx):
    """迎风对流+中心扩散显式: c + 2r <= 1
    => dt_c = 1 / (|a|/dx + 2*nu/dx^2)。"""
    return 1.0 / (abs(a) / dx + 2.0 * nu / (dx * dx))


def critical_dt_central_adv_diff(a, nu, dx):
    """中心对流+中心扩散显式 FTCS: r <= 1/2 且 c^2 <= 2r
    => dt_c = min(dx^2/(2*nu), 2*nu/a^2)。"""
    return min(dx * dx / (2.0 * nu), 2.0 * nu / (a * a))
