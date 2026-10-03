"""Von Neumann（傅里叶放大因子）稳定性分析。

约定 u_t + a u_x = nu u_xx, 谐波 u_j^n = G^n exp(i k x_j), theta = k*dx in [0, 2*pi]。
每个 G_*(theta, c, r) 返回放大因子（可能是复数）。稳定 <=> max_theta |G| <= 1。
"""

import math


# ---------- 放大因子（均为符号推导后写出的闭式解） ----------

def G_ftcs(theta, c, r):
    """显式中心差分 FTCS: u^{n+1} = u^n - c/2 (u_{j+1}-u_{j-1}) + r (u_{j+1}-2u_j+u_{j-1})."""
    return complex(1.0 - 4.0 * r * math.sin(theta / 2.0) ** 2,
                   -c * math.sin(theta))


def G_upwind(theta, c, r):
    """迎风（上风, a>=0）显式: -c(u_j-u_{j-1}) + r(三点二阶导)."""
    s = math.sin(theta / 2.0)
    return complex(1.0 - c - 4.0 * r * s * s + c * math.cos(theta),
                   -c * math.sin(theta))


def G_lax_friedrichs(theta, c, r=0.0):
    """Lax-Friedrichs（纯对流）: u_j^{n+1} = (u_{j+1}+u_{j-1})/2 - c/2 (u_{j+1}-u_{j-1})."""
    return complex(math.cos(theta), -c * math.sin(theta))


def G_lax_wendroff(theta, c, r=0.0):
    """Lax-Wendroff（纯对流）: 泰勒展开到二阶，含 c^2 的人工扩散项."""
    return complex(1.0 - 2.0 * c * c * math.sin(theta / 2.0) ** 2,
                   -c * math.sin(theta))


def G_btcs(theta, c, r):
    """隐式后向欧拉 BTCS（空间中心差分）: 右端 (1 - i c sin + 2r s^2) 的倒数，无条件稳定."""
    s2 = math.sin(theta / 2.0) ** 2
    den = complex(1.0 + 4.0 * r * s2, c * math.sin(theta))
    return 1.0 / den


def G_crank_nicolson(theta, c, r):
    """Crank-Nicolson（空间中心，时间梯形）: 无条件稳定（对对流为主问题 |G(theta=pi)|=1）."""
    s2 = math.sin(theta / 2.0) ** 2
    num = complex(1.0 - 2.0 * r * s2, -0.5 * c * math.sin(theta))
    den = complex(1.0 + 2.0 * r * s2, 0.5 * c * math.sin(theta))
    return num / den


_AMPLIFIERS = {
    "ftcs": G_ftcs,
    "upwind": G_upwind,
    "lax_friedrichs": G_lax_friedrichs,
    "lax_wendroff": G_lax_wendroff,
    "btcs": G_btcs,
    "crank_nicolson": G_crank_nicolson,
}


# ---------- 临界步长（理论结果，详见 docs/stability_derivation.md） ----------

def critical_dt(scheme, dx, a, nu):
    """返回 (dt_crit, 条件描述)。无条件稳定时 dt_crit=None。

    - FTCS:          r <= 1/2 且 c^2 <= 2r   -> dt <= min(dx^2/(2 nu), 2 nu/a^2)
    - upwind:        c + 2r <= 1              -> dt <= 1 / (a/dx + 2 nu/dx^2)
    - lax_friedrichs: c <= 1                   -> dt <= dx/a
    - lax_wendroff:  c <= 1                   -> dt <= dx/a
    - btcs / crank_nicolson: 无条件稳定        -> None
    缺省的系数（a=0 或 nu=0）按极限处理。
    """
    if scheme in ("btcs", "crank_nicolson"):
        return None, "无条件稳定 (Von Neumann): 所有 c,r 下 max|G|<=1"

    if scheme == "ftcs":
        terms, bounds = [], []
        if nu and nu > 0.0:
            terms.append(2.0 * nu / (dx * dx))
            bounds.append("r <= 1/2  (dt <= dx^2/(2 nu))")
        if a and a > 0.0:
            if nu and nu > 0.0:
                terms.append(a * a / (2.0 * nu))
                bounds.append("c^2 <= 2r  (dt <= 2 nu/a^2)")
            else:
                bounds.append("c^2 <= 2r 但 nu=0 => 无正数 dt 满足 (纯对流 FTCS 恒不稳定)")
                return 0.0, "; ".join(bounds)
        if not terms:
            return None, "退化方程 a=nu=0"
        return 1.0 / max(terms), "; ".join(bounds)

    if scheme == "upwind":
        den = (a / dx if a else 0.0) + (2.0 * nu / (dx * dx) if nu else 0.0)
        if den == 0.0:
            return None, "退化方程 a=nu=0"
        return 1.0 / den, "c + 2r <= 1  (dt <= 1/(a/dx + 2 nu/dx^2))"

    if scheme in ("lax_friedrichs", "lax_wendroff"):
        if not a:
            return None, "退化: 该格式用于纯对流，a=0"
        return dx / a, "|c| <= 1  (CFL, dt <= dx/a)"

    raise ValueError("未知格式: %r" % scheme)


# ---------- 数值扫描放大因子（用于实测验证理论曲线） ----------

def amplification(scheme, theta, c, r):
    return _AMPLIFIERS[scheme](theta, c, r)


def max_growth(scheme, c, r, ntheta=20001):
    """在 theta in [0, 2pi) 上密集扫描 max|G|。返回 (max|G|, 对应 theta)。"""
    fn = _AMPLIFIERS[scheme]
    best, arg = 0.0, 0.0
    for i in range(ntheta):
        theta = 2.0 * math.pi * i / ntheta
        mag = abs(fn(theta, c, r))
        if mag > best:
            best, arg = mag, theta
    return best, arg
