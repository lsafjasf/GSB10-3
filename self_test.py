"""Self tests: correctness, edge cases, and failure-reporting behaviour.

Run:  python3 self_test.py
"""

from sparse_precond import (
    CSRMatrix, JacobiPreconditioner, ILU0Preconditioner,
    PreconditionerError, gmres, residual_norm,
)
from test_matrices import (
    laplacian_2d, diag_dominant_spd, nonsymmetric, near_singular,
)

PASS = 0


def check(cond, label):
    global PASS
    assert cond, f"FAILED: {label}"
    PASS += 1
    print(f"  ok: {label}")


def solve_and_verify(A, b, precond, label, x0=None, tol=1e-8):
    res = gmres(A, b, x0=x0, preconditioner=precond, rtol=tol, restart=50)
    rel = residual_norm(A, res.x, b) / (sum(t * t for t in b) ** 0.5)
    check(res.converged, f"{label}: converged ({res.iterations} iters)")
    check(rel <= 10 * tol, f"{label}: true rel residual {rel:.2e} <= {10*tol:.0e}")
    return res


def main():
    print("== CSR basics ==")
    A = diag_dominant_spd(40)
    check(A.nnz > 0 and A.nbytes() < A.dense_nbytes(),
          f"CSR smaller than dense ({A.storage_report()})")
    d = A.to_dense()
    check(all(abs(A.get(i, j) - d[i][j]) < 1e-15
              for i in range(40) for j in range(40)),
          "CSR get() matches dense")
    x = [i * 0.1 for i in range(40)]
    mv = A.matvec(x)
    check(all(abs(mv[i] - sum(d[i][j] * x[j] for j in range(40))) < 1e-10
              for i in range(40)), "matvec matches dense")

    print("== Convergence on required cases ==")
    n = 200
    A = diag_dominant_spd(n)
    b = [1.0] * n
    solve_and_verify(A, b, JacobiPreconditioner(A), "diag-dominant + jacobi")
    solve_and_verify(A, b, ILU0Preconditioner(A), "diag-dominant + ilu0")

    A = nonsymmetric(150)
    b = [(-1.0) ** i for i in range(150)]
    solve_and_verify(A, b, ILU0Preconditioner(A), "non-symmetric + ilu0")

    A = near_singular(8, eps=1e-8)
    b = [1.0] * A.nrows
    solve_and_verify(A, b, ILU0Preconditioner(A),
                     "near-singular shifted Laplacian + ilu0", tol=1e-6)

    A = diag_dominant_spd(100)
    b = [1.0] * 100
    x0_bad = [1e6] * 100
    res = solve_and_verify(A, b, ILU0Preconditioner(A),
                           "bad initial guess (x0=1e6) + ilu0", x0=x0_bad)

    print("== Failure reporting (must NOT report success) ==")
    A = laplacian_2d(16)                  # mildly ill-conditioned
    b = [1.0] * A.nrows
    res = gmres(A, b, max_iter=20, rtol=1e-10, restart=50)
    check(not res.converged, "max_iter hit -> converged is False")
    check(res.break_reason and "max_iter" in res.break_reason,
          f"failure reason reported: {res.break_reason}")
    check(res.final_residual() > 1e-10,
          f"final residual reported: {res.final_residual():.2e}")

    As = laplacian_2d(8, neumann=True)   # exactly singular
    b = [1.0] * As.nrows
    res = gmres(As, b, max_iter=200, rtol=1e-10, restart=50)
    check(not res.converged, "singular system -> converged is False")
    check(res.final_residual() > 1e-6,
          f"singular: residual stagnates at {res.final_residual():.2e}")
    # Zero-pivot / zero-diagonal detection: A = [[0, 1], [1, 1]]
    Az = CSRMatrix.from_coo(2, 2, [0, 1, 1], [1, 0, 1], [1.0, 1.0, 1.0])
    try:
        ILU0Preconditioner(Az)
        check(False, "ILU(0) with zero pivot should raise")
    except PreconditionerError as e:
        check(True, f"ILU(0) zero pivot detected: {e}")
    try:
        JacobiPreconditioner(Az)
        check(False, "Jacobi with zero diagonal should raise")
    except PreconditionerError as e:
        check(True, f"Jacobi zero diagonal detected: {e}")

    print("== Edge cases ==")
    A1 = CSRMatrix.from_coo(1, 1, [0], [0], [2.0])
    res = gmres(A1, [4.0])
    check(res.converged and abs(res.x[0] - 2.0) < 1e-12, "1x1 system")

    A2 = CSRMatrix.from_coo(2, 2, [0, 1], [0, 1], [2.0, 4.0])
    res = gmres(A2, [2.0, 8.0])
    check(res.converged and res.iterations <= 2
          and abs(res.x[0] - 1.0) < 1e-12 and abs(res.x[1] - 2.0) < 1e-12,
          "diagonal 2x2 solves in <= 2 GMRES steps")
    res = gmres(A2, [2.0, 8.0], x0=[1.0, 2.0])
    check(res.converged and res.iterations == 0, "exact x0 -> 0 iterations")

    try:
        gmres(A2, [0.0, 0.0])
        check(False, "zero RHS should raise")
    except ValueError as e:
        check(True, f"zero RHS rejected: {e}")

    try:
        JacobiPreconditioner(CSRMatrix.from_coo(2, 2, [0], [0], [1.0]))
        check(False, "Jacobi with zero diagonal should raise")
    except PreconditionerError as e:
        check(True, f"Jacobi zero diagonal rejected: {e}")

    print(f"\nAll {PASS} checks passed.")


if __name__ == "__main__":
    main()
