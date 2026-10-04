"""自测：正确性对照 + 边界用例。运行：python3 tests/test_solvers.py"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from sparse_matrix import CSRMatrix, norm2
from preconditioner import (JacobiPreconditioner, ILU0Preconditioner,
                            SingularMatrixError)
from gmres import gmres
from matrices import laplacian_2d, nearly_singular, convection_diffusion

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


def dense_solve(A, b):
    """部分主元高斯消元（仅测试用，小规模）。"""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-300:
            raise ValueError("singular")
        M[col], M[piv] = M[piv], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = (M[i][n] - s) / M[i][i]
    return x


def rel_res(A, x, b):
    r = [bi - xi for bi, xi in zip(b, A.matvec(x))]
    nb = norm2(b)
    return norm2(r) / (nb if nb > 0 else 1.0)


print("== 1. CSR 基本运算与存储 ==")
A = CSRMatrix.from_rows(3, 3, [{0: 2.0, 1: -1.0},
                               {0: -1.0, 2: 3.0},
                               {1: 4.0, 2: 1.0}])
check("matvec 正确", A.matvec([1.0, 2.0, 3.0]) == [0.0, 8.0, 11.0])
check("diagonal 正确", A.diagonal() == [2.0, 0.0, 1.0])
check("CSR 存储公式 12*nnz+4*(n+1)",
      A.memory_bytes() == 12 * A.nnz + 4 * 4)
A64 = laplacian_2d(8)
check("CSR 比稠密省空间(64阶五点格式)",
      A64.memory_bytes() < A64.dense_memory_bytes())

print("== 2. 与稠密高斯消元对照（随机稀疏 SPD） ==")
random.seed(7)
n = 30
rows = []
for i in range(n):
    row = {i: 4.0}
    for j in range(n):
        if j != i and random.random() < 0.1:
            row[j] = -0.5
    rows.append(row)
Asp = CSRMatrix.from_rows(n, n, rows)
xstar = [math.sin(i + 1) for i in range(n)]
b = Asp.matvec(xstar)
xref = dense_solve(Asp.to_dense(), b)
for name, M in [("无预处理", None),
                ("Jacobi", JacobiPreconditioner(Asp)),
                ("ILU(0)", ILU0Preconditioner(Asp))]:
    res = gmres(Asp, b, M, tol=1e-10, max_iter=200, restart=30)
    err = max(abs(a - c) for a, c in zip(res["x"], xref))
    check(f"GMRES({name}) 收敛且与直接解一致",
          res["converged"] and err < 1e-6, f"err={err:.2e}")

print("== 3. 边界用例 ==")
# b = 0：精确解 x=0，应一次不迭代即收敛
res = gmres(Asp, [0.0] * n, None, tol=1e-10)
check("b=0 立即收敛且 x=0",
      res["converged"] and res["iterations"] == 0
      and max(map(abs, res["x"])) == 0.0)

# 奇异矩阵 + 不相容右端项：必须报告未收敛并给出残差
Asing = CSRMatrix.from_rows(2, 2, [{0: 1.0, 1: 1.0}, {0: 1.0, 1: 1.0}])
res = gmres(Asing, [1.0, 2.0], None, tol=1e-12, max_iter=50, restart=10)
check("奇异不相容系统报告未收敛",
      not res["converged"] and res["final_relative_residual"] > 1e-12,
      f"rel={res['final_relative_residual']:.2e}")
check("未收敛时 message 明确", "未收敛" in res["message"])

# 达到最大迭代：不得当成成功
Ahard = nearly_singular(200, eps=1e-6)
bhard = Ahard.matvec([1.0] * 200)
res = gmres(Ahard, bhard, None, tol=1e-10, max_iter=5, restart=5)
check("max_iter 用尽报告未收敛并给出残差",
      not res["converged"] and res["iterations"] == 5
      and res["final_relative_residual"] > 0
      and "未收敛" in res["message"])

# 零对角元：Jacobi 与 ILU(0) 都必须显式报错
Azero = CSRMatrix.from_rows(2, 2, [{1: 1.0}, {0: 1.0, 1: 1.0}])
try:
    JacobiPreconditioner(Azero)
    check("Jacobi 零对角报错", False)
except SingularMatrixError:
    check("Jacobi 零对角报错", True)
try:
    ILU0Preconditioner(Azero)
    check("ILU(0) 零主元报错", False)
except SingularMatrixError:
    check("ILU(0) 零主元报错", True)

# 初值恰好是解：0 次迭代
res = gmres(Asp, b, None, x0=list(xstar), tol=1e-10)
check("初值即解时 0 次迭代收敛",
      res["converged"] and res["iterations"] == 0)

# 残差历史单调记录且首项是初始相对残差
res = gmres(Asp, b, JacobiPreconditioner(Asp), tol=1e-8, max_iter=100)
hist = res["residual_history"]
check("残差历史首项=初始相对残差",
      abs(hist[0] - rel_res(Asp, [0.0] * n, b)) < 1e-15)
check("收敛时末项 <= tol", hist[-1] <= 1e-8 + 1e-15)

print("== 4. 四类矩阵场景冒烟测试 ==")
for label, A in [("对角占优", laplacian_2d(8)),
                 ("接近奇异", nearly_singular(60, 1e-4)),
                 ("非对称", convection_diffusion(8, 10.0))]:
    xs = [1.0] * A.nrows
    bb = A.matvec(xs)
    res = gmres(A, bb, ILU0Preconditioner(A), tol=1e-8,
                max_iter=300, restart=50)
    check(f"{label}: ILU(0) 收敛", res["converged"],
          f"rel={res['final_relative_residual']:.2e}")

# 很差的初值仍可收敛（判据基于残差而非误差）
A1 = laplacian_2d(8)
b1 = A1.matvec([1.0] * 64)
res = gmres(A1, b1, ILU0Preconditioner(A1),
            x0=[1e8 * math.sin(7 * (i + 1)) for i in range(64)],
            tol=1e-8, max_iter=300, restart=50)
check("初值很差(1e8 振荡)仍收敛", res["converged"])

print(f"\n通过 {PASSED} 项，失败 {FAILED} 项")
sys.exit(1 if FAILED else 0)
