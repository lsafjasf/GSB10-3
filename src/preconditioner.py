"""预处理器：Jacobi（对角）与 ILU(0)（无填充不完全 LU）。

统一接口：M.apply(r) 返回 z = M^{-1} r，供右预处理 GMRES 使用。
ILU(0) 分解严格在 CSR 结构上进行，不生成任何二维稠密数组。
"""
from bisect import bisect_left

from sparse_matrix import CSRMatrix


class SingularMatrixError(Exception):
    """预处理构造失败（零主元 / 零对角元）。"""


class JacobiPreconditioner:
    """M = diag(A)，z_i = r_i / a_ii。"""

    def __init__(self, A):
        self.diag = A.diagonal()
        for i, d in enumerate(self.diag):
            if d == 0.0:
                raise SingularMatrixError(f"Jacobi 失败：第 {i} 行对角元为零")

    def apply(self, r):
        d = self.diag
        return [ri / di for ri, di in zip(r, d)]


class ILU0Preconditioner:
    """ILU(0)：L 单位下三角、U 上三角，稀疏模式与 A 完全相同（无填充）。"""

    def __init__(self, A):
        if A.nrows != A.ncols:
            raise ValueError("ILU(0) 只适用于方阵")
        n = A.nrows
        values, col_idx, row_ptr = A.values, A.col_idx, A.row_ptr

        # 按行复制为可变的 (列列表, 值列表)，分解在原地进行，不引入填充
        cols_rows, vals_rows = [], []
        for i in range(n):
            s, e = row_ptr[i], row_ptr[i + 1]
            cols_rows.append(list(col_idx[s:e]))
            vals_rows.append(list(values[s:e]))

        for i in range(n):
            cols_i, vals_i = cols_rows[i], vals_rows[i]
            diag_pos = -1
            for pos, j in enumerate(cols_i):
                if j >= i:
                    if j == i:
                        diag_pos = pos
                    break
                # a_ij <- a_ij / u_jj，随后消去本行中 j 列以右的已有非零元
                cols_j, vals_j = cols_rows[j], vals_rows[j]
                u_jj = None
                for pj, cj in enumerate(cols_j):
                    if cj == j:
                        u_jj = vals_j[pj]
                        break
                if u_jj is None or u_jj == 0.0:
                    raise SingularMatrixError(f"ILU(0) 失败：第 {j} 行零主元")
                lij = vals_i[pos] / u_jj
                vals_i[pos] = lij
                # 行 j 中列号 > j 的部分即 U 的第 j 行
                start = bisect_left(cols_j, j + 1)
                for pj in range(start, len(cols_j)):
                    cj = cols_j[pj]
                    # 仅当 (i, cj) 属于 A 的稀疏模式时才更新（ILU(0) 核心）
                    pi = bisect_left(cols_i, cj)
                    if pi < len(cols_i) and cols_i[pi] == cj:
                        vals_i[pi] -= lij * vals_j[pj]
            if diag_pos < 0 or vals_i[diag_pos] == 0.0:
                raise SingularMatrixError(f"ILU(0) 失败：第 {i} 行零主元")

        # 拆分为 L（单位对角，仅存严格下三角）与 U（含对角）两个 CSR
        l_vals, l_cols, l_ptr = [], [], [0]
        u_vals, u_cols, u_ptr = [], [], [0]
        for i in range(n):
            cols_i, vals_i = cols_rows[i], vals_rows[i]
            cut = bisect_left(cols_i, i)
            l_cols.extend(cols_i[:cut])
            l_vals.extend(vals_i[:cut])
            l_ptr.append(len(l_vals))
            u_cols.extend(cols_i[cut:])
            u_vals.extend(vals_i[cut:])
            u_ptr.append(len(u_vals))
        self.L = CSRMatrix(n, n, l_vals, l_cols, l_ptr)
        self.U = CSRMatrix(n, n, u_vals, u_cols, u_ptr)

    def apply(self, r):
        """解 L U z = r：前代（L 单位对角）+ 回代（U）。"""
        n = self.L.nrows
        L, U = self.L, self.U
        y = [0.0] * n
        for i in range(n):
            s, e = L.row_ptr[i], L.row_ptr[i + 1]
            acc = sum(v * y[c] for v, c in zip(L.values[s:e], L.col_idx[s:e]))
            y[i] = r[i] - acc
        z = [0.0] * n
        for i in range(n - 1, -1, -1):
            s, e = U.row_ptr[i], U.row_ptr[i + 1]
            acc = 0.0
            diag = 0.0
            for v, c in zip(U.values[s:e], U.col_idx[s:e]):
                if c == i:
                    diag = v
                else:
                    acc += v * z[c]
            z[i] = (y[i] - acc) / diag
        return z
