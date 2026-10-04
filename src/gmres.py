"""重启型右预处理 GMRES（Restarted GMRES(m)），仅使用标准库。

收敛判据（显式、可复现）：
    || b - A x ||_2 / || b ||_2 <= tol          （||b|| != 0 时）
    || b - A x ||_2 <= tol                      （||b|| == 0 时）
- 每次 Arnoldi 步更新 Givens 旋转后的残差估计，估计值判停；
- 达到 max_iter 仍不满足判据时返回 converged=False，并报告真实残差，
  绝不把未收敛结果当作成功。
"""
import math

from sparse_matrix import dot, norm2, axpy


def gmres(A, b, preconditioner=None, x0=None, *, tol=1e-8, max_iter=200,
          restart=50, record_curve=True):
    n = A.nrows
    if x0 is None:
        x = [0.0] * n
    else:
        if len(x0) != n:
            raise ValueError("初值维度不符")
        x = list(x0)
    bnorm = norm2(b)
    scale = bnorm if bnorm > 0.0 else 1.0

    def precond(v):
        return preconditioner.apply(v) if preconditioner is not None else v

    history = []
    r = [bi - xi for bi, xi in zip(b, A.matvec(x))]
    rnorm = norm2(r)
    history.append(rnorm / scale)
    if rnorm / scale <= tol:
        return _result(True, 0, history, x, rnorm, scale,
                       "初始残差已满足容差")

    total_iter = 0
    while total_iter < max_iter:
        m = min(restart, max_iter - total_iter)
        beta = rnorm
        Q = [[ri / beta for ri in r]]                   # Krylov 基向量列表
        H = [[0.0] * m for _ in range(m + 1)]            # Hessenberg 上标
        g = [0.0] * (m + 1)
        g[0] = beta
        cs = [0.0] * m                                   # Givens 旋转参数
        sn = [0.0] * m

        j = 0
        converged = False
        while j < m:
            w = A.matvec(precond(Q[j]))
            # 修改的 Gram-Schmidt
            for k in range(j + 1):
                h = dot(Q[k], w)
                H[k][j] = h
                w = axpy(-h, Q[k], w)
            h_next = norm2(w)
            H[j + 1][j] = h_next
            if h_next > 0.0:
                Q.append([wi / h_next for wi in w])

            # 对第 j 列应用既往旋转
            for k in range(j):
                c, s = cs[k], sn[k]
                h1, h2 = H[k][j], H[k + 1][j]
                H[k][j] = c * h1 + s * h2
                H[k + 1][j] = -s * h1 + c * h2

            # 新旋转（处理精确收敛时 h1==0 的退化情况）
            h1, h2 = H[j][j], H[j + 1][j]
            denom = math.hypot(h1, h2)
            if denom == 0.0:
                cs[j], sn[j] = 1.0, 0.0
            else:
                cs[j] = h1 / denom
                sn[j] = h2 / denom
            H[j][j] = denom
            H[j + 1][j] = 0.0
            g[j + 1] = -sn[j] * g[j]
            g[j] = cs[j] * g[j]

            total_iter += 1
            j += 1
            est = abs(g[j])
            if record_curve:
                history.append(est / scale)
            if est / scale <= tol:
                converged = True
                break

        # 回代解上三角 R y = g（使用 j 维 Krylov 子空间）
        y = [0.0] * j
        for k in range(j - 1, -1, -1):
            s = g[k]
            for t in range(k + 1, j):
                s -= H[k][t] * y[t]
            y[k] = s / H[k][k]
        Qy = [0.0] * n
        for k, yk in enumerate(y):
            if yk != 0.0:
                Qy = axpy(yk, Q[k], Qy)
        x = axpy(1.0, precond(Qy), x)

        if converged:
            r = [bi - xi for bi, xi in zip(b, A.matvec(x))]
            rnorm = norm2(r)
            # 以真实残差为准（防止旋转估计误差误判）
            if rnorm / scale <= tol:
                return _result(True, total_iter, history, x, rnorm, scale,
                               "残差判据满足")
        else:
            r = [bi - xi for bi, xi in zip(b, A.matvec(x))]
            rnorm = norm2(r)
            history.append(rnorm / scale)
            if rnorm / scale <= tol:
                return _result(True, total_iter, history, x, rnorm, scale,
                               "重启时真实残差满足容差")

    return _result(False, total_iter, history, x, rnorm, scale,
                   f"达到最大迭代 {max_iter} 次仍未收敛")


def _result(converged, iterations, history, x, rnorm, scale, message):
    return {
        "converged": converged,
        "iterations": iterations,
        "residual_history": history,
        "x": x,
        "final_absolute_residual": rnorm,
        "final_relative_residual": rnorm / scale,
        "message": message,
    }
