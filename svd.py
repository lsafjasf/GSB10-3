"""Pure-Python singular value decomposition and low-rank approximation.

Only the Python standard library is used.  The implementation uses cyclic
one-sided Jacobi rotations: columns of B = A V are repeatedly rotated by an
orthogonal matrix V until they are mutually orthogonal.  The column norms of
the final B are singular values, B = U Sigma, and A = U Sigma V^T.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Dict, List, Optional, Sequence, Tuple

Matrix = List[List[float]]

DEFAULT_TOL = 1e-12
DEFAULT_MAX_SWEEPS = 100
EPSILON = 2.220446049250313e-16


@dataclass(frozen=True)
class SVDResult:
    """Reduced SVD of an m-by-n matrix.

    ``u`` is m-by-r, ``singular_values`` has length r, and ``v`` is n-by-n,
    where r = min(m, n).  Singular values are in non-increasing order.
    """

    u: Matrix
    singular_values: List[float]
    v: Matrix
    sweeps: int
    converged: bool
    final_off_diagonal_norm: float


def validate_matrix(a: Sequence[Sequence[float]], name: str = "matrix") -> None:
    if not a:
        raise ValueError(name + " must have at least one row")
    width = len(a[0])
    if width == 0:
        raise ValueError(name + " must have at least one column")
    for row_index, row in enumerate(a):
        if len(row) != width:
            raise ValueError(name + " row " + str(row_index) + " has inconsistent length")


def shape(a: Sequence[Sequence[float]], name: str = "matrix") -> Tuple[int, int]:
    validate_matrix(a, name)
    return len(a), len(a[0])


def zeros(rows: int, cols: int) -> Matrix:
    return [[0.0 for _ in range(cols)] for _ in range(rows)]


def identity(n: int) -> Matrix:
    result = zeros(n, n)
    for i in range(n):
        result[i][i] = 1.0
    return result


def transpose(a: Sequence[Sequence[float]]) -> Matrix:
    rows, cols = shape(a)
    return [[a[i][j] for i in range(rows)] for j in range(cols)]


def matmul(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Matrix:
    a_rows, a_cols = shape(a, "left matrix")
    b_rows, b_cols = shape(b, "right matrix")
    if a_cols != b_rows:
        raise ValueError("matrix dimensions do not align for multiplication")
    result = zeros(a_rows, b_cols)
    for i in range(a_rows):
        for k in range(a_cols):
            aik = a[i][k]
            if aik == 0.0:
                continue
            for j in range(b_cols):
                result[i][j] += aik * b[k][j]
    return result


def subtract(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Matrix:
    rows, cols = shape(a, "left matrix")
    b_rows, b_cols = shape(b, "right matrix")
    if (rows, cols) != (b_rows, b_cols):
        raise ValueError("matrix dimensions do not match for subtraction")
    return [[a[i][j] - b[i][j] for j in range(cols)] for i in range(rows)]


def frobenius_norm(a: Sequence[Sequence[float]]) -> float:
    shape(a)
    return sqrt(sum(value * value for row in a for value in row))


def _off_diagonal_norm(b: Matrix, rows: int, cols: int) -> float:
    total = 0.0
    for p in range(cols - 1):
        for q in range(p + 1, cols):
            inner_product = 0.0
            for i in range(rows):
                inner_product += b[i][p] * b[i][q]
            total += inner_product * inner_product
    return sqrt(total)


def _apply_jacobi_sweep(b: Matrix, v: Matrix, rows: int, cols: int) -> None:
    for p in range(cols - 1):
        for q in range(p + 1, cols):
            alpha = 0.0
            beta = 0.0
            gamma = 0.0
            for i in range(rows):
                bp = b[i][p]
                bq = b[i][q]
                alpha += bp * bp
                beta += bq * bq
                gamma += bp * bq

            if gamma == 0.0:
                continue

            zeta = (beta - alpha) / (2.0 * gamma)
            if zeta >= 0.0:
                tangent = 1.0 / (zeta + sqrt(1.0 + zeta * zeta))
            else:
                tangent = -1.0 / (-zeta + sqrt(1.0 + zeta * zeta))
            cosine = 1.0 / sqrt(1.0 + tangent * tangent)
            sine = cosine * tangent

            for i in range(rows):
                bp = b[i][p]
                bq = b[i][q]
                b[i][p] = cosine * bp - sine * bq
                b[i][q] = sine * bp + cosine * bq

            for i in range(cols):
                vp = v[i][p]
                vq = v[i][q]
                v[i][p] = cosine * vp - sine * vq
                v[i][q] = sine * vp + cosine * vq


def _orthonormal_left_vectors(
    b: Matrix,
    ordered_sigma: List[float],
    order: List[int],
    rows: int,
    reduced_rank: int,
) -> Matrix:
    u = zeros(rows, reduced_rank)
    largest_singular = ordered_sigma[0] if ordered_sigma else 0.0
    singular_floor = max(rows, len(ordered_sigma)) * EPSILON * largest_singular

    for j in range(reduced_rank):
        old_index = order[j]
        if ordered_sigma[j] > singular_floor:
            inverse_sigma = 1.0 / ordered_sigma[j]
            for i in range(rows):
                u[i][j] = b[i][old_index] * inverse_sigma
            continue

        candidate = [0.0 for _ in range(rows)]
        for basis_index in range(rows):
            candidate[basis_index] = 1.0
            for _ in range(2):
                for k in range(j):
                    projection = sum(candidate[i] * u[i][k] for i in range(rows))
                    for i in range(rows):
                        candidate[i] -= projection * u[i][k]
            candidate_norm = sqrt(sum(value * value for value in candidate))
            if candidate_norm > 1e-14:
                break
            candidate = [0.0 for _ in range(rows)]

        candidate_norm = sqrt(sum(value * value for value in candidate))
        if candidate_norm <= 1e-14:
            raise RuntimeError("failed to construct an orthonormal U column")
        for i in range(rows):
            u[i][j] = candidate[i] / candidate_norm

    return u


def svd(
    a: Sequence[Sequence[float]],
    tol: float = DEFAULT_TOL,
    max_sweeps: int = DEFAULT_MAX_SWEEPS,
) -> SVDResult:
    """Compute a reduced SVD using cyclic one-sided Jacobi iteration.

    A sweep rotates every column pair once.  Convergence is checked when
        ||off(B^T B)||_F <= tol * ||A||_F,
    where ||off(B^T B)||_F is the Frobenius norm of all strict upper
    off-diagonal inner products.  Default tolerance is 1e-12 and at most 100
    sweeps are allowed.
    """

    rows, cols = shape(a)
    if tol <= 0.0:
        raise ValueError("tol must be positive")
    if max_sweeps < 1:
        raise ValueError("max_sweeps must be at least 1")

    b = [[float(a[i][j]) for j in range(cols)] for i in range(rows)]
    v = identity(cols)
    reduced_rank = min(rows, cols)
    matrix_norm = frobenius_norm(b)
    convergence_scale = max(matrix_norm, 1e-300)
    target = tol * convergence_scale
    off_diagonal_norm = _off_diagonal_norm(b, rows, cols)
    completed_sweeps = 0

    for _ in range(max_sweeps):
        if off_diagonal_norm <= target:
            break
        _apply_jacobi_sweep(b, v, rows, cols)
        completed_sweeps += 1
        off_diagonal_norm = _off_diagonal_norm(b, rows, cols)
        if off_diagonal_norm <= target:
            break
    else:
        raise RuntimeError(
            "Jacobi SVD did not converge after "
            + str(max_sweeps)
            + " sweeps (off-diagonal norm "
            + repr(off_diagonal_norm)
            + ", target "
            + repr(target)
            + ")"
        )

    singular_values = [
        sqrt(sum(b[i][j] * b[i][j] for i in range(rows))) for j in range(cols)
    ]
    order = sorted(range(cols), key=lambda index: (-singular_values[index], index))
    ordered_sigma = [singular_values[index] for index in order]
    ordered_v = zeros(cols, cols)
    for new_index, old_index in enumerate(order):
        for i in range(cols):
            ordered_v[i][new_index] = v[i][old_index]

    u = _orthonormal_left_vectors(
        b, ordered_sigma, order, rows, reduced_rank
    )

    return SVDResult(
        u=u,
        singular_values=ordered_sigma[:reduced_rank],
        v=ordered_v,
        sweeps=completed_sweeps,
        converged=True,
        final_off_diagonal_norm=off_diagonal_norm,
    )


def reconstruct(result: SVDResult, rank: Optional[int] = None) -> Matrix:
    """Reconstruct sum_{i<rank} sigma_i u_i v_i^T."""

    rows = len(result.u)
    cols = len(result.v)
    reduced_rank = len(result.singular_values)
    if rank is None:
        rank = reduced_rank
    if rank < 0 or rank > reduced_rank:
        raise ValueError("rank must be between 0 and min(rows, cols)")

    approximation = zeros(rows, cols)
    for component in range(rank):
        sigma = result.singular_values[component]
        if sigma == 0.0:
            continue
        for i in range(rows):
            scaled_u = sigma * result.u[i][component]
            for j in range(cols):
                approximation[i][j] += scaled_u * result.v[j][component]
    return approximation


def low_rank_approximation(
    a: Sequence[Sequence[float]],
    rank: int,
    tol: float = DEFAULT_TOL,
    max_sweeps: int = DEFAULT_MAX_SWEEPS,
) -> Matrix:
    """Compute the rank-``rank`` truncated SVD approximation of ``a``."""

    result = svd(a, tol=tol, max_sweeps=max_sweeps)
    return reconstruct(result, rank)


def direct_residual_norm(
    a: Sequence[Sequence[float]], approximation: Sequence[Sequence[float]]
) -> float:
    """Return ||A - A_k||_F by explicitly forming A - A_k."""

    return frobenius_norm(subtract(a, approximation))


def tail_energy_error(singular_values: Sequence[float], rank: int) -> float:
    """Return sqrt(sum_{i>=rank} sigma_i^2), the theory error of truncation."""

    if rank < 0 or rank > len(singular_values):
        raise ValueError("rank must be between 0 and the reduced SVD rank")
    return sqrt(sum(sigma * sigma for sigma in singular_values[rank:]))


def compression_stats(rows: int, cols: int, rank: int) -> Dict[str, float]:
    """Storage count for (U_k, Sigma_k, V_k).

    The truncated representation stores rank*(rows + cols + 1) values:
    rows*rank values in U_k, rank singular values, and cols*rank values in V_k.
    """

    if rows <= 0 or cols <= 0:
        raise ValueError("rows and cols must be positive")
    if rank < 0 or rank > min(rows, cols):
        raise ValueError("rank must be between 0 and min(rows, cols)")
    original_values = rows * cols
    stored_values = rank * (rows + cols + 1)
    ratio = stored_values / float(original_values)
    return {
        "original_values": float(original_values),
        "stored_values": float(stored_values),
        "compression_ratio": ratio,
        "space_saving": 1.0 - ratio,
    }
