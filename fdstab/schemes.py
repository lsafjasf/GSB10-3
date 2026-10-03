"""差分格式步进实现（显式 / 隐式）与三对角求解器，仅依赖标准库。

网格: 内部点 N 个, x_j = x0 + (j-1)*dx, j=1..N; g[0], g[N+1] 为虚元。
支持边界: periodic（周期）/ dirichlet / neumann；边界值可为常数或 t 的函数。
"""

SCHEMES = (
    "ftcs", "upwind", "lax_friedrichs", "lax_wendroff",  # 显式
    "btcs", "crank_nicolson",                            # 隐式
)
EXPLICIT = ("ftcs", "upwind", "lax_friedrichs", "lax_wendroff")
IMPLICIT = ("btcs", "crank_nicolson")


# ---------------- 线性方程组求解器（纯 Python） ----------------

def thomas(low, diag, up, rhs):
    """追赶法求解三对角方程组，原地改写并返回 x。low[0]/up[-1] 不使用。"""
    n = len(diag)
    cp = [0.0] * n
    dp = [0.0] * n
    cp[0] = up[0] / diag[0]
    dp[0] = rhs[0] / diag[0]
    for i in range(1, n):
        m = diag[i] - low[i] * cp[i - 1]
        cp[i] = up[i] / m if i < n - 1 else 0.0
        dp[i] = (rhs[i] - low[i] * dp[i - 1]) / m
    x = [0.0] * n
    x[n - 1] = dp[n - 1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    return x


def cyclic_thomas(low, diag, up, rhs, wrap_l, wrap_u):
    """周期三对角（循环）方程组，Sherman-Morrison 公式。

    首行额外含 wrap_l * x[-1]，末行额外含 wrap_u * x[0]；
    对应周期耦合: 行 j=1 左端环绕系数 = low，行 j=N 右端环绕系数 = up。
    """
    n = len(diag)
    dmod = list(diag)
    dmod[0] -= wrap_l
    dmod[n - 1] -= wrap_u
    y = thomas(low, dmod, up, rhs)
    qvec = [0.0] * n
    qvec[0] = wrap_l
    qvec[n - 1] = wrap_u
    q = thomas(low, dmod, up, qvec)
    factor = (y[0] + y[n - 1]) / (1.0 + q[0] + q[n - 1])
    return [y[i] - factor * q[i] for i in range(n)]


# ---------------- 边界虚元 ----------------

def _bc_value(spec, t):
    if callable(spec):
        return float(spec(t))
    if isinstance(spec, tuple):  # ("d"|"n", value|callable)
        kind, val = spec
        v = float(val(t)) if callable(val) else float(val)
        return kind, v
    return float(spec)


def _fill_ghosts(g, dx, t, bc, bl, br):
    n = len(g) - 2
    if bc == "periodic":
        g[0] = g[n]
        g[n + 1] = g[1]
        return
    if bc == "dirichlet":
        g[0] = _bc_value(bl, t)
        g[n + 1] = _bc_value(br, t)
    elif bc == "neumann":
        ql = _bc_value(bl, t)
        qr = _bc_value(br, t)
        g[0] = g[2] - 2.0 * dx * ql
        g[n + 1] = g[n - 1] + 2.0 * dx * qr
    else:
        raise ValueError("未知边界类型: %r" % bc)


# ---------------- 显式格式 ----------------

def _step_explicit(scheme, g, c, r, dx, t, bc, bl, br):
    _fill_ghosts(g, dx, t, bc, bl, br)
    n = len(g) - 2
    old = g[1:n + 1]
    left = g[0:n]
    right = g[2:n + 2]

    if scheme == "ftcs":
        for j in range(n):
            g[j + 1] = old[j] - 0.5 * c * (right[j] - left[j]) \
                       + r * (right[j] - 2.0 * old[j] + left[j])
    elif scheme == "upwind":
        for j in range(n):
            g[j + 1] = old[j] - c * (old[j] - left[j]) \
                       + r * (right[j] - 2.0 * old[j] + left[j])
    elif scheme == "lax_friedrichs":
        if bc != "periodic":
            raise ValueError("Lax-Friedrichs 本库仅按周期边界实现（需替换空间平均）")
        for j in range(n):
            g[j + 1] = 0.5 * (right[j] + left[j]) \
                       - 0.5 * c * (right[j] - left[j])
    elif scheme == "lax_wendroff":
        if bc != "periodic":
            raise ValueError("Lax-Wendroff 本库仅按周期边界实现（需单侧边界格式）")
        for j in range(n):
            g[j + 1] = old[j] - 0.5 * c * (right[j] - left[j]) \
                       + 0.5 * c * c * (right[j] - 2.0 * old[j] + left[j])
    else:
        raise ValueError(scheme)


# ---------------- 隐式格式 ----------------

def _step_implicit(scheme, g, c, r, dx, dt, t, bc, bl, br):
    n = len(g) - 2
    periodic = bc == "periodic"
    # Neumann 虚元含新时间层未知量，这里用上一时间层显式预估（边界处理一阶），
    # Dirichlet/时间函数在 t+dt 取值。
    ghost_time = t
    if bc == "dirichlet":
        ghost_time = t + dt
    _fill_ghosts(g, dx, ghost_time, bc, bl, br)
    old = g[1:n + 1]

    if scheme == "btcs":
        low, up, d = -0.5 * c - r, 0.5 * c - r, 1.0 + 2.0 * r
        rhs = old
    else:  # crank_nicolson
        low, up, d = 0.25 * c - 0.5 * r, -0.25 * c - 0.5 * r, 1.0 + r
        rhs = [0.0] * n
        left = g[0:n]
        right = g[2:n + 2]
        for j in range(n):
            rhs[j] = ((1.0 - r) * old[j]
                      + (0.25 * c + 0.5 * r) * right[j]
                      + (-0.25 * c + 0.5 * r) * left[j])

    low_arr = [low] * n
    diag_arr = [d] * n
    up_arr = [up] * n

    if periodic:
        x = cyclic_thomas(low_arr, diag_arr, up_arr, rhs, low, up)
    else:
        b = list(rhs)
        b[0] -= low * g[0]
        b[n - 1] -= up * g[n + 1]
        x = thomas(low_arr, diag_arr, up_arr, b)

    g[1:n + 1] = x
    _fill_ghosts(g, dx, t + dt, bc, bl, br)


# ---------------- 对外接口 ----------------

def step(scheme, g, dx, dt, a, nu, t=0.0,
         bc="periodic", bc_left=0.0, bc_right=0.0):
    """单步推进，g 为含虚元的列表（原地修改）。t 为推进前时刻。"""
    c = a * dt / dx
    r = nu * dt / (dx * dx)
    if scheme in EXPLICIT:
        _step_explicit(scheme, g, c, r, dx, t, bc, bc_left, bc_right)
    elif scheme in IMPLICIT:
        _step_implicit(scheme, g, c, r, dx, dt, t, bc, bc_left, bc_right)
    else:
        raise ValueError("未知格式: %r" % scheme)


def evolve(scheme, u0, dx, dt, a, nu, nsteps,
           bc="periodic", bc_left=0.0, bc_right=0.0, t0=0.0):
    """推进 nsteps 步，返回内部点结果列表。"""
    g = [0.0] + list(u0) + [0.0]
    t = t0
    for _ in range(nsteps):
        step(scheme, g, dx, dt, a, nu, t, bc, bc_left, bc_right)
        t += dt
    return g[1:len(g) - 1]
