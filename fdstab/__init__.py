"""fdstab: 一维对流-扩散方程差分格式稳定性分析库（仅标准库）。

方程: u_t + a * u_x = nu * u_xx   (a >= 0, nu >= 0)
无量纲数: c = a*dt/dx (Courant 数), r = nu*dt/dx^2 (扩散数)
"""

from .schemes import evolve, SCHEMES
from .von_neumann import (
    amplification,
    max_growth,
    critical_dt,
    G_ftcs,
    G_upwind,
    G_lax_friedrichs,
    G_lax_wendroff,
    G_btcs,
    G_crank_nicolson,
)

__all__ = [
    "evolve",
    "SCHEMES",
    "amplification",
    "max_growth",
    "critical_dt",
    "G_ftcs",
    "G_upwind",
    "G_lax_friedrichs",
    "G_lax_wendroff",
    "G_btcs",
    "G_crank_nicolson",
]
