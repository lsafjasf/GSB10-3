"""
svd_core.py — 纯标准库实现的 SVD 与低秩近似（Python 3，仅用 math/random）

算法
----
对 B = A^T A 使用幂迭代 (power iteration) 求最大特征值/特征向量 v:
    w   = B v = A^T (A v)
    v'  = w / ||w||
    sigma = ||A v||_2,  u = A v / sigma
然后做 Hotelling 收缩 B <- B - sigma^2 v v^T, 按降序依次得到
(sigma_1,u_1,v_1), ..., (sigma_r,u_r,v_r)。

不显式维护收缩矩阵: 迭代每一步都用两次经典 Gram-Schmidt (等价 DGKS 修正
Gram-Schmidt) 把向量投影到与已接受的 v_1..v_{j-1} 正交的子空间,
在数学上等价于在收缩后的 B 上迭代, 同时补偿收缩引入的数值漂移。

收敛判据 (三者同时满足, tol 默认 1e-12)
---------------------------------------
  (a) 奇异值变化  |sigma_new - sigma_old| < tol * sigma_ref
  (b) 特征方程残差  ||(B - lambda I) v||_2 < tol * lambda_ref
      (lambda = v^T B v = ||A v||_2^2 为 Rayleigh 商)
  (c) 特征向量角度  1 - |v_old^T v_new| < 100 * tol
  其中 sigma_ref = max(sigma_1, sigma_new), lambda_ref = sigma_ref^2,
  sigma_1 为已求得的最大奇异值 —— 即判据相对于矩阵整体谱范数归一,
  这样小奇异值 (如 1e-8) 也能在双精度内收敛。
  (c) 用于避免残差很小但方向还没稳定时的“假收敛”。

最大迭代次数与重启
------------------
  每个奇异向量最多 max_iter 次 (默认 20000); 未收敛则换随机初值重启,
  最多额外重启 2 次 (共 3 轮); 仍不收敛抛出 RuntimeError。

零奇异值判停 (数值秩)
---------------------
  lambda = ||A v||_2^2 <= zero_tol^2,
  zero_tol = 8 * eps * max(m,n) * ||A||_F  (eps = 2.22e-16, 双精度),
  即按机器精度与矩阵尺度判定数值秩 (与 LAPACK dgesdd 的奇异值截断同量级),
  与迭代收敛容差 tol 解耦。
"""

import math
import random


# ---------------------------------------------------------------------------
# 基础线性代数
# ---------------------------------------------------------------------------


def shape(A):
    return len(A), len(A[0])


def transpose(A):
    m, n = shape(A)
    return [[A[i][j] for i in range(m)] for j in range(n)]


def matvec(A, x):
    m, n = shape(A)
    y = [0.0] * m
    for i in range(m):
        row = A[i]
        s = 0.0
        for j in range(n):
            s += row[j] * x[j]
        y[i] = s
    return y


def dot(x, y):
    return math.fsum(xi * yi for xi, yi in zip(x, y))


def norm2(x):
    return math.sqrt(math.fsum(xi * xi for xi in x))


def scale(x, a):
    return [a * xi for xi in x]


def gram_schmidt_twice(x, Q):
    """从 x 中去掉其在 Q 中各向量上的分量; 做两遍 (DGKS), 增强正交性。"""
    for _ in range(2):
        for q in Q:
            c = dot(q, x)
            if c:
                for i in range(len(x)):
                    x[i] -= c * q[i]
    return x


def normalize(x):
    nrm = norm2(x)
    if nrm == 0.0:
        return x, 0.0
    return scale(x, 1.0 / nrm), nrm


# ---------------------------------------------------------------------------
# 单个主奇异三元组
# ---------------------------------------------------------------------------


def _power_triplet(A, At, accepted_V, tol, max_iter, restarts_left,
                   zero_tol, rng, sigma1):
    """返回 (sigma, u, v, n_iter, status); status ∈ converged/zero/restarted"""
    m, n = shape(A)

    v = [rng.gauss(0.0, 1.0) for _ in range(n)]
    v = gram_schmidt_twice(v, accepted_V)
    v, _ = normalize(v)

    sigma_old = -1.0
    rel_resid = float("inf")
    rel_dsigma = float("inf")
    angle_change = float("inf")
    it = 0

    for it in range(1, max_iter + 1):
        Av = matvec(A, v)
        lam = dot(Av, Av)                      # Rayleigh 商

        if lam <= zero_tol * zero_tol:
            return 0.0, None, None, it, "zero-subspace"

        w = matvec(At, Av)                    # w = B v
        w = gram_schmidt_twice(w, accepted_V)
        wnorm = norm2(w)
        if wnorm == 0.0:
            return 0.0, None, None, it, "zero-subspace"
        v_new = scale(w, 1.0 / wnorm)

        # 在 v_new 上重算残差 (B - lambda I) v_new
        Avn = matvec(A, v_new)
        Bvn = gram_schmidt_twice(matvec(At, Avn), accepted_V)
        resid = norm2([Bvn[i] - lam * v_new[i] for i in range(n)])

        sigma_new = math.sqrt(lam)
        sigma_ref = max(sigma1, sigma_new)
        lambda_ref = sigma_ref * sigma_ref
        rel_resid = resid / lambda_ref
        rel_dsigma = (abs(sigma_new - sigma_old) / sigma_ref
                      if sigma_old >= 0.0 else float("inf"))
        angle_change = 1.0 - min(abs(dot(v, v_new)), 1.0)

        if (sigma_old >= 0.0
                and rel_resid < tol
                and rel_dsigma < tol
                and angle_change < 100.0 * tol):
            u = scale(Avn, 1.0 / sigma_new)
            return sigma_new, u, v_new, it, "converged"

        sigma_old = sigma_new
        v = v_new

    if restarts_left > 0:
        return _power_triplet(A, At, accepted_V, tol, max_iter,
                              restarts_left - 1, zero_tol, rng, sigma1)
    raise RuntimeError(
        "幂迭代在 max_iter=%d 次内未收敛: 残差=%.3e, σ相对变化=%.3e, "
        "角度变化=%.3e" % (max_iter, rel_resid, rel_dsigma, angle_change))


# ---------------------------------------------------------------------------
# 完整瘦型 SVD: (U[m x r], S[长度 r], Vt[r x n])
# ---------------------------------------------------------------------------


def svd(A, tol=1e-12, max_iter=20000, seed=12345):
    A = [list(map(float, row)) for row in A]
    m, n = shape(A)
    if m == 0 or n == 0:
        return [], [], []

    At = transpose(A)
    fro2 = math.fsum(x * x for row in A for x in row)
    eps = 2.220446049250313e-16
    zero_tol = 8.0 * eps * max(m, n) * math.sqrt(max(fro2, 0.0))
    rng = random.Random(seed)

    Ucols, S, V = [], [], []
    for _ in range(min(m, n)):
        sigma1 = S[0] if S else 0.0
        sigma, u, v, iters, status = _power_triplet(
            A, At, V, tol, max_iter, 2, zero_tol, rng, sigma1)
        if status == "zero-subspace" or sigma <= zero_tol:
            break
        # u = Av/sigma 会把 Av 的舍入误差放大 1/sigma 倍, 且误差方向主要
        # 落在已接受的 u_1..u_{j-1} 子空间内, 这里做一次再正交修正
        u = gram_schmidt_twice(u, Ucols)
        u, _ = normalize(u)
        Ucols.append(u)
        S.append(sigma)
        V.append(v)

    r = len(S)
    Um = [[Ucols[k][i] for k in range(r)] for i in range(m)]
    Vt = [[V[k][j] for j in range(n)] for k in range(r)]
    return Um, S, Vt


# ---------------------------------------------------------------------------
# 低秩近似与误差
# ---------------------------------------------------------------------------


def low_rank_reconstruct(U, S, Vt, k):
    """A_k = U[:, :k] diag(S[:k]) Vt[:k, :]; k=0 返回 m x n 全零矩阵。"""
    m = len(U)
    r = len(S)
    n = len(Vt[0])
    k = max(0, min(k, r))
    Ak = [[0.0] * n for _ in range(m)]
    for t in range(k):
        coef_t = S[t]
        vt = Vt[t]
        for i in range(m):
            coef = U[i][t] * coef_t
            row = Ak[i]
            for j in range(n):
                row[j] += coef * vt[j]
    return Ak


def reconstruct_from(A_shape, U, S, Vt, k):
    """与 low_rank_reconstruct 相同, 但秩为 0 时也能按原始形状建零矩阵。"""
    m, n = A_shape
    if len(S) == 0:
        return [[0.0] * n for _ in range(m)]
    return low_rank_reconstruct(U, S, Vt, k)


def frobenius_norm(A):
    return math.sqrt(math.fsum(x * x for row in A for x in row))


def residual_matrix(A, B):
    return [[A[i][j] - B[i][j] for j in range(len(A[0]))]
            for i in range(len(A))]


def spectral_norm_power(R, tol=1e-11, max_iter=10000, seed=777):
    """独立对拍: 直接对残差 R 幂迭代求其 2-范数 (R 的最大奇异值)。"""
    if not R or not R[0]:
        return 0.0
    m, n = shape(R)
    Rt = transpose(R)
    rng = random.Random(seed)
    v, _ = normalize([rng.gauss(0.0, 1.0) for _ in range(n)])
    sigma_old = 0.0
    for _ in range(max_iter):
        w = matvec(Rt, matvec(R, v))
        wn = norm2(w)
        if wn == 0.0:
            return 0.0
        v = scale(w, 1.0 / wn)
        sigma = norm2(matvec(R, v))
        if abs(sigma - sigma_old) <= tol * max(sigma, 1.0):
            return sigma
        sigma_old = sigma
    return sigma


def errors_by_rank(A, U, S, Vt, spec_check_ranks=None):
    """
    对 k = 0..r 逐秩计算:
      fro_direct   : 直接构造 A_k 再算 ||A - A_k||_F
      fro_formula  : 奇异值尾部能量 sqrt(sum_{i>k} sigma_i^2)
      spec_formula : 2-范数误差 sigma_{k+1} (下一个被截掉的奇异值)
      spec_power   : 直接对残差矩阵幂迭代得到的 2-范数 (独立对拍)
      rel_fro      : Frobenius 相对误差
      compression  : 压缩比 m*n / (k*(m+n+1)), k=0 记为 inf
    存储模型: 原矩阵存 m*n 个数; 低秩形式存 U(m*k)+S(k)+Vt(k*n) 个数。
    """
    m, n = shape(A)
    r = len(S)
    total_fro2 = math.fsum(x * x for row in A for x in row)
    if spec_check_ranks is None:
        spec_check_ranks = set(range(r + 1))

    # 后缀和从最小奇异值开始累加, 避免 "大数 - 大数" 的灾难性抵消
    suffix2 = [0.0] * (r + 1)
    for i in range(r - 1, -1, -1):
        suffix2[i] = suffix2[i + 1] + S[i] * S[i]
    results = []
    for k in range(r + 1):
        tail2 = suffix2[k]
        Ak = reconstruct_from((m, n), U, S, Vt, k)
        R = residual_matrix(A, Ak)
        fro_direct = frobenius_norm(R)
        fro_formula = math.sqrt(max(tail2, 0.0))
        spec_formula = S[k] if k < r else 0.0
        spec_power = (spectral_norm_power(R) if k in spec_check_ranks
                      else None)

        rank_store = k * (m + n + 1)
        compression = (m * n / rank_store) if rank_store else float("inf")

        results.append({
            "k": k,
            "fro_direct": fro_direct,
            "fro_formula": fro_formula,
            "fro_gap": abs(fro_direct - fro_formula),
            "spec_formula": spec_formula,
            "spec_power": spec_power,
            "spec_gap": (abs(spec_formula - spec_power)
                         if spec_power is not None else None),
            "rel_fro": (fro_direct / math.sqrt(total_fro2)
                        if total_fro2 > 0 else 0.0),
            "compression": compression,
        })
    return results
