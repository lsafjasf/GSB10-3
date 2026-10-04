"""CSR (Compressed Sparse Row) 稀疏矩阵存储结构，仅依赖标准库。

存储布局（array 模块，紧凑二进制存储，非 Python 对象列表）：
  values  : array('d')  非零元数值，每个 8 字节
  col_idx : array('i')  每个非零元的列下标，每个 4 字节
  row_ptr : array('i')  每行起始位置，长度 n+1，每个 4 字节

稠密存储同规模需要 8*n^2 字节；CSR 只需要 12*nnz + 4*(n+1) 字节。
"""
from array import array
import math


class CSRMatrix:
    """压缩稀疏行（CSR）矩阵。行内列下标保持升序。"""

    def __init__(self, nrows, ncols, values, col_idx, row_ptr):
        if len(row_ptr) != nrows + 1:
            raise ValueError("row_ptr 长度必须为 nrows+1")
        if len(values) != len(col_idx):
            raise ValueError("values 与 col_idx 长度不一致")
        if row_ptr[-1] != len(values):
            raise ValueError("row_ptr[-1] 必须等于非零元个数")
        self.nrows = nrows
        self.ncols = ncols
        self.values = array("d", values)
        self.col_idx = array("i", col_idx)
        self.row_ptr = array("i", row_ptr)

    # ---------- 构造 ----------
    @classmethod
    def from_rows(cls, nrows, ncols, rows):
        """rows: 长度为 nrows 的列表，每行是 {列号: 值} 字典。"""
        values, col_idx, row_ptr = [], [], [0]
        for row in rows:
            for j in sorted(row):
                v = row[j]
                if v != 0.0:
                    col_idx.append(j)
                    values.append(float(v))
            row_ptr.append(len(values))
        return cls(nrows, ncols, values, col_idx, row_ptr)

    # ---------- 基本运算 ----------
    def matvec(self, x):
        """y = A @ x"""
        if len(x) != self.ncols:
            raise ValueError("向量维度与矩阵列数不符")
        values, col_idx, row_ptr = self.values, self.col_idx, self.row_ptr
        y = [0.0] * self.nrows
        for i in range(self.nrows):
            s, e = row_ptr[i], row_ptr[i + 1]
            y[i] = sum(v * xv for v, xv in zip(values[s:e], map(x.__getitem__, col_idx[s:e])))
        return y

    def diagonal(self):
        diag = [0.0] * self.nrows
        for i in range(self.nrows):
            for k in range(self.row_ptr[i], self.row_ptr[i + 1]):
                if self.col_idx[k] == i:
                    diag[i] = self.values[k]
                    break
        return diag

    def to_dense(self):
        """仅用于小规模测试对照，主流程不使用。"""
        dense = [[0.0] * self.ncols for _ in range(self.nrows)]
        for i in range(self.nrows):
            for k in range(self.row_ptr[i], self.row_ptr[i + 1]):
                dense[i][self.col_idx[k]] = self.values[k]
        return dense

    # ---------- 存储占用 ----------
    @property
    def nnz(self):
        return len(self.values)

    def memory_bytes(self):
        """CSR 实际占用字节数：8*nnz + 4*nnz + 4*(n+1)。"""
        return 8 * self.nnz + 4 * self.nnz + 4 * (self.nrows + 1)

    def dense_memory_bytes(self):
        """等规模稠密矩阵（float64）占用字节数。"""
        return 8 * self.nrows * self.ncols

    def storage_report(self):
        csr = self.memory_bytes()
        dense = self.dense_memory_bytes()
        return {
            "n": self.nrows,
            "nnz": self.nnz,
            "avg_nnz_per_row": self.nnz / self.nrows,
            "csr_bytes": csr,
            "dense_bytes": dense,
            "ratio": dense / csr if csr else float("inf"),
        }


# ---------- 向量工具（标准库实现） ----------
def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def norm2(a):
    return math.sqrt(sum(x * x for x in a))


def axpy(alpha, x, y):
    """返回 alpha*x + y（新向量）。"""
    return [alpha * xi + yi for xi, yi in zip(x, y)]
