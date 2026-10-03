"""Preconditioners for sparse Krylov solvers.

Each preconditioner M approximates A and exposes solve(r) -> z with z ~ A^{-1} r.
"""

from array import array

from .csr_matrix import CSRMatrix


class PreconditionerError(Exception):
    """Raised when a preconditioner cannot be built (e.g. zero pivot)."""


class IdentityPreconditioner:
    name = "none"

    def solve(self, r):
        return list(r)


class JacobiPreconditioner:
    """M = diag(A).  Cheap, effective for diagonally dominant systems."""

    name = "jacobi"

    def __init__(self, A):
        if A.nrows != A.ncols:
            raise ValueError("Jacobi requires a square matrix")
        self.inv_diag = []
        for i in range(A.nrows):
            d = A.get(i, i)
            if d == 0.0:
                raise PreconditionerError(
                    f"Jacobi: zero diagonal entry at row {i}")
            self.inv_diag.append(1.0 / d)

    def solve(self, r):
        return [d * ri for d, ri in zip(self.inv_diag, r)]


class ILU0Preconditioner:
    """ILU(0): incomplete LU keeping exactly the sparsity pattern of A.

    A ~ L*U where L is unit lower, U upper, both stored together in one
    CSR matrix with the same pattern as A.  Raises PreconditionerError on
    a zero pivot (typical for singular / structurally deficient matrices).
    """

    name = "ilu0"

    def __init__(self, A):
        if A.nrows != A.ncols:
            raise ValueError("ILU(0) requires a square matrix")
        n = A.nrows
        lu_vals = array('d', A.values)          # copy, overwritten in place
        col_idx, row_ptr = A.col_idx, A.row_ptr

        for i in range(n):
            row_start, row_end = row_ptr[i], row_ptr[i + 1]
            for p in range(row_start, row_end):
                k = col_idx[p]
                if k >= i:
                    break                     # only strict lower part
                # a_ik <- a_ik / a_kk  (U diagonal of row k)
                kk = A.find(k, k)
                if kk is None or lu_vals[kk] == 0.0:
                    raise PreconditionerError(
                        f"ILU(0): zero pivot at row {k} "
                        f"(matrix may be singular or need reordering)")
                lu_vals[p] /= lu_vals[kk]
                a_ik = lu_vals[p]
                # a_ij <- a_ij - a_ik * a_kj for j > k present in both rows
                k_end = row_ptr[k + 1]
                for q in range(row_ptr[k], k_end):
                    j = col_idx[q]
                    if j <= k:
                        continue
                    t = A.find(i, j)
                    if t is not None:
                        lu_vals[t] -= a_ik * lu_vals[q]

        self.n = n
        self.lu = CSRMatrix(n, n, lu_vals, col_idx, row_ptr)

    def solve(self, r):
        """Forward substitution (unit L) then backward substitution (U)."""
        n = self.n
        vals, cols, ptr = self.lu.values, self.lu.col_idx, self.lu.row_ptr
        y = list(r)
        for i in range(n):                    # L y = r, L unit lower
            s = y[i]
            for p in range(ptr[i], ptr[i + 1]):
                if cols[p] < i:
                    s -= vals[p] * y[cols[p]]
            y[i] = s
        x = y
        for i in range(n - 1, -1, -1):        # U x = y
            s = x[i]
            diag = 0.0
            for p in range(ptr[i], ptr[i + 1]):
                j = cols[p]
                if j > i:
                    s -= vals[p] * x[j]
                elif j == i:
                    diag = vals[p]
            x[i] = s / diag
        return x
