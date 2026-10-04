"""一维对流扩散方程差分求解器（仅标准库）。

方程: u_t + a u_x = nu u_xx,  x in [0, L], a >= 0
显式: 迎风/中心 对流 + 中心扩散
隐式: 迎风对流 + 中心扩散 的向后 Euler（无条件稳定）
边界: periodic / dirichlet / outflow / neumann
"""


# ---------------------------------------------------------------- Thomas 算法

def thomas(lower, diag, upper, rhs):
    """求解一般三对角线性方程组（返回新列表，不改入参）。

    lower[0] 与 upper[n-1] 无意义。
    """
    n = len(diag)
    cp = [0.0] * n
    dp = [0.0] * n
    cp[0] = upper[0] / diag[0]
    dp[0] = rhs[0] / diag[0]
    for i in range(1, n):
        m = diag[i] - lower[i] * cp[i - 1]
        cp[i] = upper[i] / m if i < n - 1 else 0.0
        dp[i] = (rhs[i] - lower[i] * dp[i - 1]) / m
    x = [0.0] * n
    x[-1] = dp[-1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    return x


def cyclic_thomas(lower, diag, upper, rhs, corner_lower, corner_upper):
    """循环三对角方程组（周期边界），Sherman-Morrison 方法。

    矩阵 = 一般三对角 T，外加角元 A[n-1][0]=corner_lower, A[0][n-1]=corner_upper。
    """
    n = len(diag)
    gamma = -diag[0]
    dmod = diag[:]
    dmod[0] = diag[0] - gamma
    dmod[-1] = diag[-1] - corner_lower * corner_upper / gamma
    u_vec = [gamma] + [0.0] * (n - 2) + [corner_lower]
    v_vec = [1.0] + [0.0] * (n - 2) + [corner_upper / gamma]

    y = thomas(lower, dmod, upper, rhs)
    q = thomas(lower, dmod, upper, u_vec)
    vq = sum(v_vec[i] * q[i] for i in range(n))
    vy = sum(v_vec[i] * y[i] for i in range(n))
    factor = vy / (1.0 + vq)
    return [y[i] - factor * q[i] for i in range(n)]


# ---------------------------------------------------------------- 显式推进

def _explicit_step(u, c, r, adv):
    n = len(u)
    u_new = [0.0] * n
    if adv == 'upwind':
        for i in range(n):
            im = (i - 1) % n
            ip = (i + 1) % n
            u_new[i] = (u[i] - c * (u[i] - u[im])
                        + r * (u[ip] - 2.0 * u[i] + u[im]))
    elif adv == 'central':
        for i in range(n):
            im = (i - 1) % n
            ip = (i + 1) % n
            u_new[i] = (u[i] - 0.5 * c * (u[ip] - u[im])
                        + r * (u[ip] - 2.0 * u[i] + u[im]))
    else:
        raise ValueError("adv 只能是 'upwind' 或 'central'")
    return u_new


def explicit_solve(u0, dx, dt, nsteps, a, nu, bc='periodic',
                   adv='upwind', g_left=None, g_right=None,
                   record_every=None):
    """显式推进 nsteps 步。

    u0: 初值。
      - periodic: 周期点 0..N-1（u(N)=u(0) 为同一点）。
      - 其余: 网格点 0..N，长度 N+1。
    g_left/g_right: dirichlet/outflow 边界值函数 t -> float，默认 0。
    record_every: 若给定，每该步数记录一次 (step, u 副本)。
    返回 (u_final, records)。
    """
    g_left = g_left or (lambda t: 0.0)
    g_right = g_right or (lambda t: 0.0)
    c = a * dt / dx
    r = nu * dt / (dx * dx)
    u = u0[:]
    records = []

    if bc == 'periodic':
        for step in range(1, nsteps + 1):
            u = _explicit_step(u, c, r, adv)
            if record_every and step % record_every == 0:
                records.append((step, u[:]))
        return u, records

    for step in range(1, nsteps + 1):
        t_new = step * dt
        n = len(u) - 1  # 逻辑点 0..N
        un = u
        u_new = [0.0] * (n + 1)
        for i in range(0, n + 1):
            ul = un[i - 1] if i > 0 else un[1]          # ghost u_{-1}=u_1
            ur = un[i + 1] if i < n else un[n - 1]      # ghost u_{N+1}=u_{N-1}
            if adv == 'upwind':
                u_new[i] = (un[i] - c * (un[i] - ul)
                            + r * (ur - 2.0 * un[i] + ul))
            else:
                u_new[i] = (un[i] - 0.5 * c * (ur - ul)
                            + r * (ur - 2.0 * un[i] + ul))
        if bc == 'dirichlet':
            u_new[0] = g_left(t_new)
            u_new[n] = g_right(t_new)
        elif bc == 'outflow':
            u_new[0] = g_left(t_new)
            u_new[n] = u_new[n - 1]       # 出口零梯度
        elif bc == 'neumann':
            u_new[0] = u_new[1]
            u_new[n] = u_new[n - 1]
        else:
            raise ValueError("未知 bc: %s" % bc)
        u = u_new
        if record_every and step % record_every == 0:
            records.append((step, u[:]))
    return u, records


# ---------------------------------------------------------------- 隐式推进

def implicit_solve(u0, dx, dt, nsteps, a, nu, bc='periodic',
                   g_left=None, g_right=None, record_every=None):
    """向后 Euler 隐式（迎风对流 + 中心扩散），无条件稳定。"""
    g_left = g_left or (lambda t: 0.0)
    g_right = g_right or (lambda t: 0.0)
    c = a * dt / dx
    r = nu * dt / (dx * dx)
    u = u0[:]
    records = []

    for step in range(1, nsteps + 1):
        t_new = step * dt
        if bc == 'periodic':
            m = len(u)
            lower = [-(c + r)] * m
            diag = [1.0 + c + 2.0 * r] * m
            upper = [-r] * m
            u = cyclic_thomas(lower, diag, upper, u, -r, -(c + r))
        else:
            n = len(u) - 1  # 点 0..N
            size = n + 1
            lower = [0.0] * size
            diag = [0.0] * size
            upper = [0.0] * size
            rhs = u[:]
            # 内部点 1..N-1
            for i in range(1, n):
                lower[i] = -(c + r)
                diag[i] = 1.0 + c + 2.0 * r
                upper[i] = -r
            # 左端点 i=0（ghost u_-1=u_1, 迎风 advection 也用 u_1）
            lower[0] = 0.0
            diag[0] = 1.0 + c + 2.0 * r
            upper[0] = -(c + 2.0 * r)
            # 右端点 i=N（ghost u_{N+1}=u_{N-1}）
            lower[n] = -(c + 2.0 * r)
            diag[n] = 1.0 + c + 2.0 * r
            upper[n] = 0.0
            if bc == 'dirichlet':
                gl, gr = g_left(t_new), g_right(t_new)
                diag[0] = 1.0; upper[0] = 0.0; rhs[0] = gl
                lower[n] = 0.0; diag[n] = 1.0; rhs[n] = gr
                # 已知边界值移入右端项, 同时清零对应矩阵元
                rhs[1] += (c + r) * gl
                lower[1] = 0.0
                rhs[n - 1] += r * gr
                upper[n - 1] = 0.0
            elif bc == 'outflow':
                gl = g_left(t_new)
                diag[0] = 1.0; upper[0] = 0.0; rhs[0] = gl
                rhs[1] += (c + r) * gl
                lower[1] = 0.0
            elif bc == 'neumann':
                pass  # 零梯度行已构造
            else:
                raise ValueError("未知 bc: %s" % bc)
            u = thomas(lower, diag, upper, rhs)
        if record_every and step % record_every == 0:
            records.append((step, u[:]))
    return u, records


# ---------------------------------------------------------------- 范数工具

def l2_norm(v, dx=1.0):
    return (dx * sum(x * x for x in v)) ** 0.5


def l2_error(u_num, u_exact, dx=1.0):
    return l2_norm([x - y for x, y in zip(u_num, u_exact)], dx)


def linf_error(u_num, u_exact):
    return max(abs(x - y) for x, y in zip(u_num, u_exact))
