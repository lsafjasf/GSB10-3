"""CSR (Compressed Sparse Row) sparse matrix, pure standard library.

Storage layout (nnz = number of nonzeros, n = rows):
    values : array('d'), nnz doubles
    col_idx: array('i'), nnz int32
    row_ptr: array('i'), n+1 int32

Total = 12*nnz + 4*(n+1) bytes, versus 8*n*n bytes for a dense
list-of-lists of floats.  See CSRMatrix.storage_report().
"""

from array import array
from bisect import bisect_left


class CSRMatrix:
    """Immutable-ish CSR matrix.  Build via CSRMatrix.from_coo()."""

    def __init__(self, nrows, ncols, values, col_idx, row_ptr):
        self.nrows = nrows
        self.ncols = ncols
        self.values = values      # array('d')
        self.col_idx = col_idx    # array('i'), sorted within each row
        self.row_ptr = row_ptr    # array('i'), length nrows + 1

    # ------------------------------------------------------------------ build
    @classmethod
    def from_coo(cls, nrows, ncols, rows, cols, vals):
        """Build from COO triplets; duplicate (i, j) entries are summed."""
        acc = {}
        for i, j, v in zip(rows, cols, vals):
            if not (0 <= i < nrows and 0 <= j < ncols):
                raise IndexError(f"COO index ({i}, {j}) out of range")
            acc[(i, j)] = acc.get((i, j), 0.0) + v

        row_ptr = array('i', [0]) * (nrows + 1)
        for (i, _j) in acc:
            row_ptr[i + 1] += 1
        for i in range(nrows):
            row_ptr[i + 1] += row_ptr[i]

        nnz = len(acc)
        values = array('d', [0.0]) * nnz
        col_idx = array('i', [0]) * nnz
        cursor = array('i', row_ptr[:-1])
        for (i, j), v in sorted(acc.items()):
            p = cursor[i]
            values[p] = v
            col_idx[p] = j
            cursor[i] = p + 1
        return cls(nrows, ncols, values, col_idx, row_ptr)

    # ------------------------------------------------------------------ info
    @property
    def nnz(self):
        return len(self.values)

    def nbytes(self):
        """Actual bytes used by the CSR arrays."""
        return (self.values.buffer_info()[1] * self.values.itemsize
                + self.col_idx.buffer_info()[1] * self.col_idx.itemsize
                + self.row_ptr.buffer_info()[1] * self.row_ptr.itemsize)

    def dense_nbytes(self):
        """Bytes an equivalent dense n x n float64 array would need."""
        return 8 * self.nrows * self.ncols

    def storage_report(self):
        csr = self.nbytes()
        dense = self.dense_nbytes()
        ratio = (dense / csr) if csr else float('inf')
        return (f"CSR: {csr} B (nnz={self.nnz}) | dense: {dense} B "
                f"| dense/CSR = {ratio:.1f}x")

    # ------------------------------------------------------------- accessors
    def find(self, i, j):
        """Position of (i, j) in values, or None if structurally zero."""
        lo, hi = self.row_ptr[i], self.row_ptr[i + 1]
        p = bisect_left(self.col_idx, j, lo, hi)
        if p < hi and self.col_idx[p] == j:
            return p
        return None

    def get(self, i, j, default=0.0):
        p = self.find(i, j)
        return self.values[p] if p is not None else default

    def diag(self):
        return [self.get(i, i) for i in range(min(self.nrows, self.ncols))]

    # ------------------------------------------------------------- operators
    def matvec(self, x):
        if len(x) != self.ncols:
            raise ValueError("matvec dimension mismatch")
        y = [0.0] * self.nrows
        values, col_idx, row_ptr = self.values, self.col_idx, self.row_ptr
        for i in range(self.nrows):
            s = 0.0
            for p in range(row_ptr[i], row_ptr[i + 1]):
                s += values[p] * x[col_idx[p]]
            y[i] = s
        return y

    def to_dense(self):
        d = [[0.0] * self.ncols for _ in range(self.nrows)]
        for i in range(self.nrows):
            for p in range(self.row_ptr[i], self.row_ptr[i + 1]):
                d[i][self.col_idx[p]] = self.values[p]
        return d
