"""各情形下的精确解（用于误差度量）。"""

import math


def advected_sine(x, t, a, L, k=1):
    """纯对流（周期）: u(x,t) = sin(2*pi*k*(x - a t)/L)。"""
    return math.sin(2.0 * math.pi * k * (x - a * t) / L)


def decaying_sine(x, t, nu, L, k=1):
    """纯扩散（周期）: u(x,t) = sin(2*pi*k*x/L) * exp(-nu*(2*pi*k/L)^2 t)。"""
    lam = 2.0 * math.pi * k / L
    return math.sin(lam * x) * math.exp(-nu * lam * lam * t)


def adv_diff_sine(x, t, a, nu, L, k=1):
    """对流扩散（周期）: 平移且衰减的正弦模态。"""
    lam = 2.0 * math.pi * k / L
    return (math.sin(lam * (x - a * t))
            * math.exp(-nu * lam * lam * t))
