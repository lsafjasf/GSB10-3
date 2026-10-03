"""Benchmark: preconditioner comparison + residual curves + storage report.

Run:  python3 run_demo.py
Outputs (results/):
    iterations_comparison.csv  - iters / residual / solution error / status
    residual_curves.csv        - full true relative residual histories
    storage_comparison.csv     - CSR vs dense bytes for every matrix
"""

import csv
import math
import os
import random

from sparse_precond import (
    JacobiPreconditioner, ILU0Preconditioner, gmres,
)
from test_matrices import (
    laplacian_2d, diag_dominant_spd, nonsymmetric, near_singular,
)

RESULTS = os.path.join(os.path.dirname(__file__), "results")
RTOL = 1e-8
MAX_ITER = 500
RESTART = 50


def known_rhs(A, seed):
    """Random exact solution x_exact and consistent b = A x_exact."""
    rng = random.Random(seed)
    x_exact = [rng.uniform(-1.0, 1.0) for _ in range(A.nrows)]
    return x_exact, A.matvec(x_exact)


def rel_error(x, x_exact):
    num = math.sqrt(sum((a - b) ** 2 for a, b in zip(x, x_exact)))
    den = math.sqrt(sum(b * b for b in x_exact))
    return num / den


def make_cases():
    A1 = diag_dominant_spd(300)
    _x1, b1 = known_rhs(A1, 11)
    A2 = nonsymmetric(300)
    _x2, b2 = known_rhs(A2, 22)
    A3 = near_singular(32, eps=1e-8)          # N = 1024, cond ~ 8e8
    _x3, b3 = known_rhs(A3, 33)
    A4 = diag_dominant_spd(300)
    _x4, b4 = known_rhs(A4, 44)
    return [
        ("diag_dominant",     A1, b1, None,             _x1),
        ("non_symmetric",     A2, b2, None,             _x2),
        ("near_singular",     A3, b3, None,             _x3),
        ("bad_initial_guess", A4, b4, [1e6] * A4.nrows, _x4),
    ]


def preconditioners_for(A):
    return [("none", None),
            ("jacobi", JacobiPreconditioner(A)),
            ("ilu0", ILU0Preconditioner(A))]


def main():
    os.makedirs(RESULTS, exist_ok=True)
    cases = make_cases()

    with open(os.path.join(RESULTS, "storage_comparison.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "n", "nnz", "csr_bytes", "dense_bytes",
                    "dense_over_csr"])
        for name, A, _b, _x0, _xe in cases:
            w.writerow([name, A.nrows, A.nnz, A.nbytes(), A.dense_nbytes(),
                        f"{A.dense_nbytes() / A.nbytes():.1f}"])

    iter_rows = []
    curve_rows = []
    print(f"rtol={RTOL}, max_iter={MAX_ITER}, restart={RESTART}\n")

    for name, A, b, x0, x_exact in cases:
        print(f"=== {name}  (n={A.nrows}, nnz={A.nnz}) ===")
        print(f"    storage: {A.storage_report()}")
        for pc_name, pc in preconditioners_for(A):
            res = gmres(A, b, x0=x0, preconditioner=pc, rtol=RTOL,
                        max_iter=MAX_ITER, restart=RESTART)
            status = "converged" if res.converged else f"FAILED({res.break_reason})"
            ferr = rel_error(res.x, x_exact)
            print(f"    {pc_name:6s}: {res.iterations:4d} iters, "
                  f"rel res = {res.final_residual():.3e}, "
                  f"rel solution error = {ferr:.3e}, {status}")
            iter_rows.append([name, pc_name, A.nrows, res.iterations,
                              f"{res.final_residual():.3e}", f"{ferr:.3e}",
                              status])
            for it, rr in enumerate(res.residual_history):
                curve_rows.append([name, pc_name, it, f"{rr:.6e}"])
        print()

    with open(os.path.join(RESULTS, "iterations_comparison.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "preconditioner", "n", "iterations",
                    "final_relative_residual",
                    "relative_solution_error", "status"])
        w.writerows(iter_rows)

    with open(os.path.join(RESULTS, "residual_curves.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "preconditioner", "iteration",
                    "relative_residual"])
        w.writerows(curve_rows)

    print("Singular system demonstration (Neumann Laplacian, 8x8):")
    As = laplacian_2d(8, neumann=True)
    res = gmres(As, [1.0] * As.nrows, preconditioner=JacobiPreconditioner(As),
                rtol=1e-10, max_iter=100, restart=50)
    print("    " + res.summary())
    print("    -> converged stays False; residual cannot be driven down; "
          "never reported as success")
    print("\nWrote results/iterations_comparison.csv, "
          "results/residual_curves.csv, results/storage_comparison.csv")


if __name__ == "__main__":
    main()
