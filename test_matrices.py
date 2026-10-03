"""Deterministic test matrices (fixed seeds) covering the required cases."""

import random

from sparse_precond import CSRMatrix


def laplacian_2d(n, neumann=False):
    """5-point Laplacian on an n x n grid (N = n*n unknowns).

    Dirichlet (default): SPD, moderately conditioned.
    Neumann: diag = number of neighbours -> exactly singular (constants
    in the null space); used to exercise failure reporting.
    """
    N = n * n
    rows, cols, vals = [], [], []

    def idx(i, j):
        return i * n + j

    for i in range(n):
        for j in range(n):
            k = idx(i, j)
            nb = 0
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ni, nj = i + di, j + dj
                if 0 <= ni < n and 0 <= nj < n:
                    rows.append(k); cols.append(idx(ni, nj)); vals.append(-1.0)
                    nb += 1
            rows.append(k); cols.append(k)
            vals.append(float(nb) if neumann else 4.0)
    return CSRMatrix.from_coo(N, N, rows, cols, vals)


def diag_dominant_spd(n, seed=1):
    """Random SPD, strictly diagonally dominant (diag = 4 + sum |offdiag|)."""
    rng = random.Random(seed)
    rows, cols, vals = [], [], []
    extra = [0.0] * n
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.08:
                v = rng.uniform(-1.0, 1.0)
                rows += [i, j]; cols += [j, i]; vals += [v, v]
                extra[i] += abs(v); extra[j] += abs(v)
    for i in range(n):
        rows.append(i); cols.append(i); vals.append(4.0 + extra[i])
    return CSRMatrix.from_coo(n, n, rows, cols, vals)


def nonsymmetric(n, seed=2):
    """Non-symmetric matrix with positive diagonal dominance."""
    rng = random.Random(seed)
    rows, cols, vals = [], [], []
    for i in range(n):
        rows.append(i); cols.append(i); vals.append(4.0)
    for _ in range(2 * n):
        i = rng.randrange(n)
        j = rng.randrange(n)
        if i != j:
            rows.append(i); cols.append(j)
            vals.append(rng.uniform(-1.0, 1.0))
    return CSRMatrix.from_coo(n, n, rows, cols, vals)


def shifted_laplacian(n, eps=1e-8):
    """5-point Laplacian + eps*I: smallest eigenvalue eps, cond ~ 8/eps.

    Symmetric positive definite but extremely close to singular.
    """
    A = laplacian_2d(n)
    rows = [i for i in range(A.nrows)
            for _ in range(A.row_ptr[i], A.row_ptr[i + 1])]
    rows.extend(range(A.nrows))
    cols = list(A.col_idx) + list(range(A.nrows))
    vals = list(A.values) + [eps] * A.nrows
    return CSRMatrix.from_coo(A.nrows, A.nrows, rows, cols, vals)


def near_singular(n, eps=1e-8):
    """Near-singular SPD matrix (shifted Laplacian), cond ~ 8/eps."""
    return shifted_laplacian(n, eps)
