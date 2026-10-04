"""测试矩阵生成器：覆盖对角占优、接近奇异、非对称等情形。

所有矩阵直接以 CSR 结构构造，不经过任何二维稠密数组。
"""
from sparse_matrix import CSRMatrix


def laplacian_2d(nx, ny=None, shift=0.0, diag_variation=None):
    """二维五点差分 Laplacian（严格对角占优 SPD）。

    shift: 整体对角平移；diag_variation: 可选函数 f(行号)->非负增量，
    使对角元非均匀（此时 Jacobi 预处理不再退化为恒等缩放）。"""
    ny = ny or nx
    n = nx * ny

    def idx(ix, iy):
        return iy * nx + ix

    rows = []
    for iy in range(ny):
        for ix in range(nx):
            row = {}
            i = idx(ix, iy)
            if ix > 0:
                row[idx(ix - 1, iy)] = -1.0
            if ix < nx - 1:
                row[idx(ix + 1, iy)] = -1.0
            if iy > 0:
                row[idx(ix, iy - 1)] = -1.0
            if iy < ny - 1:
                row[idx(ix, iy + 1)] = -1.0
            d = 4.0 + shift
            if diag_variation is not None:
                d += diag_variation(i)
            row[i] = d
            rows.append(row)
    return CSRMatrix.from_rows(n, n, rows)


def nearly_singular(n, eps=1e-6):
    """接近奇异：1D Laplacian 加微小对角平移 eps，条件数 ~ O(1/eps)。

    弱对角占优（对角 = 2+eps，非对角和 = 2），eps 越小越接近奇异。
    """
    rows = []
    for i in range(n):
        row = {}
        if i > 0:
            row[i - 1] = -1.0
        if i < n - 1:
            row[i + 1] = -1.0
        row[i] = 2.0 + eps
        rows.append(row)
    return CSRMatrix.from_rows(n, n, rows)


def convection_diffusion(nx, conv=10.0):
    """对流扩散方程（中心差分），conv 较大时为强非对称矩阵。

    对角 4，x 方向非对称系数 -(1±conv/2)，y 方向对称 -1。
    """
    ny = nx
    n = nx * ny

    def idx(ix, iy):
        return iy * nx + ix

    a_w = -(1.0 + conv / 2.0)
    a_e = -(1.0 - conv / 2.0)
    rows = []
    for iy in range(ny):
        for ix in range(nx):
            row = {}
            i = idx(ix, iy)
            if ix > 0:
                row[idx(ix - 1, iy)] = a_w
            if ix < nx - 1:
                row[idx(ix + 1, iy)] = a_e
            if iy > 0:
                row[idx(ix, iy - 1)] = -1.0
            if iy < ny - 1:
                row[idx(ix, iy + 1)] = -1.0
            row[i] = 4.0
            rows.append(row)
    return CSRMatrix.from_rows(n, n, rows)


def exact_solution(n):
    """光滑精确解 x*_i = sin((i+1)*pi/(n+1))，用于构造右端项 b = A x*。"""
    import math
    return [math.sin((i + 1) * math.pi / (n + 1)) for i in range(n)]


def parabola_solution(n):
    """抛物线型精确解 x*_i = (i+1)(n-i)/(n+1)，非特征向量，谱成分丰富。"""
    return [(i + 1) * (n - i) / (n + 1) for i in range(n)]
