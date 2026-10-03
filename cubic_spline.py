"""三次样条插值库（仅标准库）。

支持两种边界条件：
  - "natural"：自然边界，S''(x0) = S''(xn) = 0（缺省，端点条件缺失时使用）
  - ("clamped", d0, dn)：固定端点一阶导数 S'(x0) = d0, S'(xn) = dn

数学模型（以节点二阶导数 M_i = S''(x_i) 为未知量）：

  记 h_i = x_{i+1} - x_i，区间 [x_i, x_{i+1}] 上

      S(x) = M_i  (x_{i+1}-x)^3 / (6 h_i)
           + M_{i+1} (x-x_i)^3 / (6 h_i)
           + (y_i   - M_i   h_i^2/6) (x_{i+1}-x)/h_i
           + (y_{i+1} - M_{i+1} h_i^2/6) (x-x_i)/h_i

  内节点一阶导数连续条件给出三对角方程组（i = 1..n-1）：

      h_{i-1} M_{i-1} + 2(h_{i-1}+h_i) M_i + h_i M_{i+1}
          = 6 [ (y_{i+1}-y_i)/h_i - (y_i-y_{i-1})/h_{i-1} ]

  边界行：
      natural : M_0 = 0,  M_n = 0
      clamped : 2 h_0 M_0 + h_0 M_1 = 6[(y_1-y_0)/h_0 - d0]
                h_{n-1} M_{n-1} + 2 h_{n-1} M_n = 6[dn - (y_n-y_{n-1})/h_{n-1}]

  方程组用 Thomas 算法（三对角追赶法）求解。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import List, Sequence, Tuple, Union

BoundaryCondition = Union[str, Tuple[str, float, float]]


class CubicSpline:
    """一维三次样条插值。"""

    def __init__(
        self,
        x: Sequence[float],
        y: Sequence[float],
        bc: BoundaryCondition = "natural",
    ) -> None:
        if len(x) != len(y):
            raise ValueError("x 与 y 长度必须一致")
        n = len(x)
        if n < 2:
            raise ValueError("至少需要 2 个节点")
        self.x: List[float] = [float(v) for v in x]
        self.y: List[float] = [float(v) for v in y]
        self.n = n
        self.h: List[float] = [self.x[i + 1] - self.x[i] for i in range(n - 1)]
        if any(hi <= 0 for hi in self.h):
            raise ValueError("x 必须严格递增")

        if bc == "natural":
            self.bc_type = "natural"
            self.d0 = self.dn = None
        elif isinstance(bc, tuple) and len(bc) == 3 and bc[0] == "clamped":
            self.bc_type = "clamped"
            self.d0, self.dn = float(bc[1]), float(bc[2])
        else:
            raise ValueError("bc 必须是 'natural' 或 ('clamped', d0, dn)")

        # 构造并求解三对角方程组，得到节点二阶导数 M
        self.A, self.rhs = self._build_system()
        self.M: List[float] = _solve_tridiagonal(self.A, self.rhs)

    # ------------------------------------------------------------------
    # 方程组构造
    # ------------------------------------------------------------------
    def _build_system(self) -> Tuple[List[List[float]], List[float]]:
        """构造关于 M 的 n x n 三对角线性方程组 A M = rhs。"""
        n, h, x, y = self.n, self.h, self.x, self.y
        A = [[0.0] * n for _ in range(n)]
        rhs = [0.0] * n

        # 第一行：左端点边界
        if self.bc_type == "natural":
            A[0][0] = 1.0
            rhs[0] = 0.0
        else:  # clamped: 2 h0 M0 + h0 M1 = 6[(y1-y0)/h0 - d0]
            A[0][0] = 2.0 * h[0]
            A[0][1] = h[0]
            rhs[0] = 6.0 * ((y[1] - y[0]) / h[0] - self.d0)

        # 内节点行
        for i in range(1, n - 1):
            A[i][i - 1] = h[i - 1]
            A[i][i] = 2.0 * (h[i - 1] + h[i])
            A[i][i + 1] = h[i]
            rhs[i] = 6.0 * (
                (y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1]
            )

        # 最后一行：右端点边界
        if self.bc_type == "natural":
            A[n - 1][n - 1] = 1.0
            rhs[n - 1] = 0.0
        else:  # clamped: h_{n-1} M_{n-1} + 2 h_{n-1} M_n = 6[dn - slope]
            A[n - 1][n - 2] = h[n - 2]
            A[n - 1][n - 1] = 2.0 * h[n - 2]
            rhs[n - 1] = 6.0 * (self.dn - (y[n - 1] - y[n - 2]) / h[n - 2])
        return A, rhs

    # ------------------------------------------------------------------
    # 求值
    # ------------------------------------------------------------------
    def _segment(self, t: float) -> int:
        """返回 t 所在区间下标 i，t 在端点外时取最近区间（外延）。"""
        if t <= self.x[0]:
            return 0
        if t >= self.x[-1]:
            return self.n - 2
        return bisect_right(self.x, t) - 1

    def _eval(self, t: float, order: int) -> float:
        i = self._segment(t)
        h = self.h[i]
        a = (self.x[i + 1] - t) / h  # 左端权重
        b = (t - self.x[i]) / h      # 右端权重
        Mi, Mj = self.M[i], self.M[i + 1]
        yi, yj = self.y[i], self.y[i + 1]
        if order == 0:
            return (
                Mi * a ** 3 * h * h / 6.0
                + Mj * b ** 3 * h * h / 6.0
                + (yi - Mi * h * h / 6.0) * a
                + (yj - Mj * h * h / 6.0) * b
            )
        if order == 1:
            return (
                -Mi * a * a * h / 2.0
                + Mj * b * b * h / 2.0
                + (yj - yi) / h
                - (Mj - Mi) * h / 6.0
            )
        if order == 2:
            return Mi * a + Mj * b
        raise ValueError("order 只支持 0/1/2")

    def __call__(self, t: float) -> float:
        return self._eval(t, 0)

    def derivative(self, t: float, order: int = 1) -> float:
        return self._eval(t, order)

    # ------------------------------------------------------------------
    # 连续性检查数据
    # ------------------------------------------------------------------
    def continuity_report(self) -> List[dict]:
        """返回每个内节点左右两侧 S、S'、S'' 的对比数据。"""
        eps = 1e-9
        rows = []
        for i in range(1, self.n - 1):
            xi = self.x[i]
            row = {"knot": xi, "index": i}
            for order, name in ((0, "S"), (1, "S1"), (2, "S2")):
                left = self._eval(xi - eps, order)
                right = self._eval(xi + eps, order)
                row[name + "_left"] = left
                row[name + "_right"] = right
                row[name + "_gap"] = abs(left - right)
            rows.append(row)
        return rows


def _solve_tridiagonal(A: List[List[float]], b: List[float]) -> List[float]:
    """Thomas 算法解三对角方程组（A 为稠密存储，仅取三条对角线）。"""
    n = len(b)
    lower = [0.0] * n  # 下对角 a_i（行 i 的列 i-1）
    diag = [0.0] * n   # 主对角 b_i
    upper = [0.0] * n  # 上对角 c_i（行 i 的列 i+1）
    for i in range(n):
        diag[i] = A[i][i]
        if i > 0:
            lower[i] = A[i][i - 1]
        if i < n - 1:
            upper[i] = A[i][i + 1]

    # 前向消元
    c = [0.0] * n
    d = [0.0] * n
    c[0] = upper[0] / diag[0]
    d[0] = b[0] / diag[0]
    for i in range(1, n):
        m = diag[i] - lower[i] * c[i - 1]
        if m == 0.0:
            raise ArithmeticError("三对角矩阵奇异，无法求解")
        c[i] = upper[i] / m if i < n - 1 else 0.0
        d[i] = (b[i] - lower[i] * d[i - 1]) / m

    # 回代
    xsol = [0.0] * n
    xsol[-1] = d[-1]
    for i in range(n - 2, -1, -1):
        xsol[i] = d[i] - c[i] * xsol[i + 1]
    return xsol
