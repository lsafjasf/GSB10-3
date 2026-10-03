"""Restarted right-preconditioned GMRES, pure standard library.

Solves A x = b.  Convergence is decided on the *true* relative residual
||b - A x|| / ||b|| (not the recurrence value), so a converged flag is
never reported without verification.
"""

import math

from .preconditioners import IdentityPreconditioner


class GMRESResult:
    def __init__(self, x, converged, iterations, residual_history,
                 break_reason=None):
        self.x = x
        self.converged = converged
        self.iterations = iterations                 # Arnoldi steps taken
        self.residual_history = residual_history     # true rel. residuals
        self.break_reason = break_reason

    def final_residual(self):
        return self.residual_history[-1] if self.residual_history else float('nan')

    def summary(self):
        status = "CONVERGED" if self.converged else f"FAILED ({self.break_reason})"
        return (f"{status}: {self.iterations} iterations, "
                f"final true rel. residual = {self.final_residual():.3e}")


def norm(v):
    return math.sqrt(sum(t * t for t in v))


def residual_norm(A, x, b):
    return norm([bi - yi for bi, yi in zip(b, A.matvec(x))])


def gmres(A, b, x0=None, *, preconditioner=None, rtol=1e-8, atol=0.0,
          max_iter=1000, restart=50, record=True):
    """Right-preconditioned GMRES(m).

    Stops when ||b - A x|| <= max(rtol * ||b||, atol).
    Returns GMRESResult; converged=False is mandatory if max_iter is
    reached or the Arnoldi process breaks down before tolerance.
    """
    if preconditioner is None:
        preconditioner = IdentityPreconditioner()
    n = len(b)
    if x0 is None:
        x = [0.0] * n
    else:
        x = list(x0)

    bnorm = norm(b)
    if bnorm == 0.0:
        raise ValueError("Right-hand side b is the zero vector; "
                         "relative residual is undefined (use x=0 trivially).")

    tol = max(rtol * bnorm, atol)
    history = []

    rnorm = residual_norm(A, x, b)
    if record:
        history.append(rnorm / bnorm)
    if rnorm <= tol:
        return GMRESResult(x, True, 0, history)

    iters = 0
    while iters < max_iter:
        r = [bi - yi for bi, yi in zip(b, A.matvec(x))]
        beta = norm(r)
        V = [[ri / beta for ri in r]]                # orthonormal basis
        m = min(restart, max_iter - iters)
        H = [[0.0] * m for _ in range(m + 1)]        # upper Hessenberg
        cs = [0.0] * m
        sn = [0.0] * m
        g = [0.0] * (m + 1)
        g[0] = beta

        j = 0
        breakdown = False
        while j < m:
            iters += 1
            z = preconditioner.solve(V[j])
            w = A.matvec(z)
            for i in range(j + 1):
                hij = sum(wk * vk for wk, vk in zip(w, V[i]))
                H[i][j] = hij
                w = [wk - hij * vk for wk, vk in zip(w, V[i])]
            h_next = norm(w)
            H[j + 1][j] = h_next

            if h_next > 1e-14:
                V.append([wk / h_next for wk in w])
            else:
                breakdown = True                      # lucky breakdown

            # Apply existing Givens rotations, then rotate the new column.
            for i in range(j):
                c, s = cs[i], sn[i]
                H[i][j], H[i + 1][j] = (
                    c * H[i][j] + s * H[i + 1][j],
                    -s * H[i][j] + c * H[i + 1][j],
                )
            h1, h2 = H[j][j], H[j + 1][j]
            den = math.hypot(h1, h2)
            if den < 1e-300:
                c, s = 1.0, 0.0
            else:
                c, s = h1 / den, h2 / den
            cs[j], sn[j] = c, s
            H[j][j] = c * h1 + s * h2
            H[j + 1][j] = 0.0
            g[j], g[j + 1] = c * g[j] + s * g[j + 1], \
                -s * g[j] + c * g[j + 1]

            if record:
                history.append(abs(g[j + 1]) / bnorm)

            j += 1
            if breakdown:
                break
            if abs(g[j]) <= tol:
                break                     # estimated residual small enough
            if iters >= max_iter:
                break

        # Solve the k x k least-squares problem (H y = g) by back substitution
        k = j
        y = [0.0] * k
        for i in range(k - 1, -1, -1):
            s = g[i] - sum(H[i][t] * y[t] for t in range(i + 1, k))
            if H[i][i] == 0.0:
                y[i] = 0.0
            else:
                y[i] = s / H[i][i]

        for t in range(k):
            zt = preconditioner.solve(V[t])
            for i in range(n):
                x[i] += y[t] * zt[i]

        # Verify with the true residual (no false success).
        true_rnorm = residual_norm(A, x, b)
        if record:
            history[-1] = true_rnorm / bnorm
        if true_rnorm <= tol:
            return GMRESResult(x, True, iters, history)
        if iters >= max_iter:
            return GMRESResult(
                x, False, iters, history,
                break_reason=f"max_iter={max_iter} reached")

    return GMRESResult(x, False, iters, history,
                       break_reason=f"max_iter={max_iter} reached")
