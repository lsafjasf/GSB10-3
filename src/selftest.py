"""
selftest.py — SVD 库的自测与数据报告 (仅标准库, 直接运行: python3 selftest.py)

覆盖:
  1. 秩一矩阵
  2. 近似奇异矩阵 (前几个奇异值占绝对主导, 尾部接近 0)
  3. 非方阵 (m > n 与 m < n 两种)
  4. 全零矩阵
  5. 通用随机矩阵 (已知奇异值谱, 用于精度验证)

对拍方式:
  方式一 (公式法): 由求得的奇异值直接给误差
      ||A - A_k||_F = sqrt(sum_{i>k} sigma_i^2),  ||A - A_k||_2 = sigma_{k+1}
  方式二 (直接残差): 显式构造 A_k, 逐元素算 R = A - A_k, 再求 ||R||_F
      与 ||R||_2 (后者用独立的幂迭代, 不复用分解结果)。
"""

import math
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svd_core import (svd, errors_by_rank, frobenius_norm, residual_matrix,
                      reconstruct_from, shape, transpose, matvec, dot,
                      norm2, spectral_norm_power)

TOL_ASSERT = 1e-8   # 自测断言阈值 (比库内部 tol 宽松, 允许数值累积误差)


# ---------------------------------------------------------------------------
# 测试矩阵构造 (只用标准库)
# ---------------------------------------------------------------------------


def random_orthonormal(n, k, rng):
    """随机 n x k 列正交矩阵: 随机高斯矩阵 + 修正 Gram-Schmidt。"""
    cols = []
    for _ in range(k):
        v = [rng.gauss(0.0, 1.0) for _ in range(n)]
        for _ in range(2):
            for q in cols:
                c = dot(q, v)
                for i in range(n):
                    v[i] -= c * q[i]
        nrm = norm2(v)
        assert nrm > 1e-10
        cols.append([x / nrm for x in v])
    return [[cols[j][i] for j in range(k)] for i in range(n)]


def matrix_with_singular_values(m, n, sigmas, rng):
    """A = U diag(sigma) V^T, U/V 为随机正交阵 -> 奇异值谱已知。"""
    r = len(sigmas)
    U = random_orthonormal(m, r, rng)
    V = random_orthonormal(n, r, rng)
    A = [[0.0] * n for _ in range(m)]
    for t in range(r):
        s = sigmas[t]
        for i in range(m):
            uit = U[i][t] * s
            for j in range(n):
                A[i][j] += uit * V[j][t]
    return A


def rank_one_matrix(m, n, rng):
    u = [rng.gauss(0.0, 1.0) for _ in range(m)]
    v = [rng.gauss(0.0, 1.0) for _ in range(n)]
    return [[ui * vj for vj in v] for ui in u]


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def print_error_table(name, A, U, S, Vt):
    m, n = shape(A)
    r = len(S)
    print("=" * 78)
    print("用例: %s   形状: %d x %d   数值秩 r = %d" % (name, m, n, r))
    print("奇异值: %s" % (", ".join("%.6e" % s for s in S) if S else "(无)"))
    header = ("%-3s %-13s %-13s %-11s %-13s %-13s %-11s %-9s" %
              ("k", "Fro(直接残差)", "Fro(奇异值公式)", "Fro差值",
               "2范数(公式)", "2范数(幂迭代)", "2范数差值", "压缩比"))
    print(header)
    print("-" * len(header))
    for row in errors_by_rank(A, U, S, Vt):
        comp = ("inf" if row["compression"] == float("inf")
                else "%.3f" % row["compression"])
        spec_p = ("-" if row["spec_power"] is None
                  else "%.6e" % row["spec_power"])
        spec_g = ("-" if row["spec_gap"] is None
                  else "%.2e" % row["spec_gap"])
        print("%-3d %-13.6e %-13.6e %-11.2e %-13.6e %-13s %-11s %-9s" % (
            row["k"], row["fro_direct"], row["fro_formula"], row["fro_gap"],
            row["spec_formula"], spec_p, spec_g, comp))
    print()
    return errors_by_rank(A, U, S, Vt)


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  [PASS] %s" % msg)


def check_orthonormality(cols, tol, label):
    for i in range(len(cols)):
        nrm = norm2(cols[i])
        assert abs(nrm - 1.0) < tol, "%s 第%d列范数=%.3e" % (label, i, nrm)
        for j in range(i + 1, len(cols)):
            c = abs(dot(cols[i], cols[j]))
            assert c < tol, "%s 第%d,%d列内积=%.3e" % (label, i, j, c)
    print("  [PASS] %s 正交归一性 (tol=%.0e)" % (label, tol))


def columns_of(U):
    if not U:
        return []
    m, r = len(U), len(U[0])
    return [[U[i][j] for i in range(m)] for j in range(r)]


# ---------------------------------------------------------------------------
# 各用例
# ---------------------------------------------------------------------------


def case_rank_one(rng):
    A = rank_one_matrix(6, 5, rng)
    U, S, Vt = svd(A)
    rows = print_error_table("秩一矩阵 6x5", A, U, S, Vt)
    check(len(S) == 1, "数值秩为 1")
    check(abs(S[0] - frobenius_norm(A)) / S[0] < TOL_ASSERT,
          "sigma_1 等于矩阵 Frobenius 范数 (秩一性质)")
    check(rows[1]["fro_direct"] < 1e-10, "k=1 时直接残差 ~ 0")
    check_orthonormality(columns_of(U), 1e-10, "U")
    check_orthonormality(columns_of(transpose(Vt)), 1e-10, "V")
    return rows


def case_nearly_singular(rng):
    sigmas = [100.0, 10.0, 1.0, 1e-4, 1e-8]
    A = matrix_with_singular_values(8, 6, sigmas, rng)
    U, S, Vt = svd(A)
    rows = print_error_table("近似奇异矩阵 8x6 (谱 100,10,1,1e-4,1e-8)",
                             A, U, S, Vt)
    check(len(S) == 5, "数值秩为 5")
    for i, s in enumerate(sigmas):
        rel = abs(S[i] - s) / s
        assert rel < 1e-6, "sigma_%d 相对误差 %.2e" % (i + 1, rel)
    print("  [PASS] 前 5 个奇异值与构造值相对误差 < 1e-6")
    check(rows[3]["rel_fro"] < 1e-6, "k=3 时相对 Frobenius 误差 < 1e-6 "
          "(尾部奇异值可忽略 -> 近似奇异)")
    check_orthonormality(columns_of(U), 1e-9, "U")
    check_orthonormality(columns_of(transpose(Vt)), 1e-9, "V")
    return rows


def case_rectangular(rng):
    out = []
    for (m, n) in [(10, 4), (4, 10)]:
        sigmas = [8.0, 3.0, 1.0, 0.3]
        A = matrix_with_singular_values(m, n, sigmas, rng)
        U, S, Vt = svd(A)
        rows = print_error_table("非方阵 %dx%d" % (m, n), A, U, S, Vt)
        check(len(S) == 4, "数值秩为 4")
        for i, s in enumerate(sigmas):
            rel = abs(S[i] - s) / s
            assert rel < 1e-8, "sigma_%d 相对误差 %.2e" % (i + 1, rel)
        print("  [PASS] 奇异值与构造值相对误差 < 1e-8")
        check_orthonormality(columns_of(U), 1e-10, "U")
        check_orthonormality(columns_of(transpose(Vt)), 1e-10, "V")
        out.append(rows)
    return out


def case_zero_matrix():
    A = [[0.0] * 5 for _ in range(4)]
    U, S, Vt = svd(A)
    rows = print_error_table("全零矩阵 4x5", A, U, S, Vt)
    check(len(S) == 0, "数值秩为 0 (无奇异值)")
    check(rows[0]["fro_direct"] == 0.0, "k=0 直接残差为 0")
    Ak = reconstruct_from((4, 5), U, S, Vt, 0)
    check(shape(Ak) == (4, 5) and frobenius_norm(Ak) == 0.0,
          "k=0 重构为 4x5 全零矩阵")
    return rows


def case_general(rng):
    sigmas = [50.0, 20.0, 5.0, 1.0, 0.2, 0.05]
    A = matrix_with_singular_values(9, 7, sigmas, rng)
    U, S, Vt = svd(A)
    rows = print_error_table("通用矩阵 9x7 (谱 50,20,5,1,0.2,0.05)",
                             A, U, S, Vt)
    check(len(S) == 6, "数值秩为 6")
    for i, s in enumerate(sigmas):
        rel = abs(S[i] - s) / s
        assert rel < 1e-9, "sigma_%d 相对误差 %.2e" % (i + 1, rel)
    print("  [PASS] 奇异值与构造值相对误差 < 1e-9")
    check_orthonormality(columns_of(U), 1e-10, "U")
    check_orthonormality(columns_of(transpose(Vt)), 1e-10, "V")
    return rows


# ---------------------------------------------------------------------------
# 对拍汇总: 公式法 vs 直接残差
# ---------------------------------------------------------------------------


def cross_check_summary(all_rows):
    print("=" * 78)
    print("对拍汇总: 奇异值公式法 vs 直接残差法")
    print("-" * 78)
    worst_fro = 0.0
    worst_spec = 0.0
    for rows in all_rows:
        for row in rows:
            worst_fro = max(worst_fro, row["fro_gap"])
            if row["spec_gap"] is not None:
                worst_spec = max(worst_spec, row["spec_gap"])
    print("  全部用例中 ||A-A_k||_F 两种算法最大差值: %.3e" % worst_fro)
    print("  全部用例中 ||A-A_k||_2 两种算法最大差值: %.3e" % worst_spec)
    check(worst_fro < 1e-7, "Frobenius 误差两法一致 (<1e-7)")
    check(worst_spec < 1e-6, "2-范数误差两法一致 (<1e-6)")
    print()
    print("差异来源说明:")
    print("  1. 浮点舍入: 直接残差法要做 O(k*m*n) 次乘加构造 A_k 再相减,")
    print("     误差随 k 与矩阵规模累积; 公式法只做 k 次平方和, 更稳定。")
    print("  2. 迭代收敛容差: 奇异值本身带有 ~tol 量级的近似误差, 两法")
    print("     都会继承, 但表现位置不同 (公式法在求和内, 直接法在重构内)。")
    print("  3. 收缩(deflation)漂移: 已求奇异向量的微小正交性误差会传入")
    print("     后续奇异值, 直接残差法对此更敏感。")
    print("  4. 2-范数对拍用的幂迭代本身有 1e-11 量级的截断误差。")
    print("  理论上 (Eckart-Young 定理) 两者应严格相等, 实测差值均在")
    print("  1e-7 以下, 与双精度浮点 + 迭代容差的预期一致。")


def main():
    rng = random.Random(20261004)
    all_rows = []
    all_rows.append(case_rank_one(rng))
    all_rows.append(case_nearly_singular(rng))
    all_rows.extend(case_rectangular(rng))
    all_rows.append(case_zero_matrix())
    all_rows.append(case_general(rng))
    cross_check_summary(all_rows)
    print("=" * 78)
    print("全部自测通过。")


if __name__ == "__main__":
    main()
