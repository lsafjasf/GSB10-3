"""test_linesearch.py — 线搜索库自测（仅标准库 unittest）

覆盖情形：
  1. 强凸函数（病态二次型，条件数 100）
  2. 平坦区域（四次函数，极小点处 Hessian 奇异）
  3. 初始步长过大（alpha0 = 1e8，远超稳定步长）
  4. 边界受限（盒式约束，无约束极小点在盒外，解落在边界上）
另含：Armijo 条件验证、单调下降断言的正反例、非下降方向报错、
     插值公式正确性、失败路径不死循环。

运行: python3 -m unittest test_linesearch.py -v
"""

import unittest
from math import sqrt

from linesearch import (
    assert_monotone_decreasing,
    backtracking_line_search,
    interpolation_line_search,
    gradient_descent,
)

METHODS = ["backtracking", "interpolation"]


# ---------------------------------------------------------------------------
# 测试问题
# ---------------------------------------------------------------------------

def quad_f(x):          # 强凸: f = 0.5*(x1^2 + 100*x2^2), 极小 0 @ (0,0)
    return 0.5 * (x[0] ** 2 + 100.0 * x[1] ** 2)


def quad_g(x):
    return [x[0], 100.0 * x[1]]


def quartic_f(x):       # 平坦: f = (x1^2 + x2^2)^2, 极小点 Hessian 为 0
    r2 = x[0] ** 2 + x[1] ** 2
    return r2 * r2


def quartic_g(x):
    r2 = x[0] ** 2 + x[1] ** 2
    return [4.0 * x[0] * r2, 4.0 * x[1] * r2]


# 耦合病态二次型 A=[[10,9],[9,10]]（特征值 19 和 1），无约束极小点在盒外；
# 盒 [0,1]^2 上的解为边界点 (1,0)，f* = 4。
C_TARGET = [3.0, -2.0]

def bounded_f(x):
    d0, d1 = x[0] - C_TARGET[0], x[1] - C_TARGET[1]
    return 0.5 * (10.0 * d0 * d0 + 18.0 * d0 * d1 + 10.0 * d1 * d1)

def bounded_g(x):
    d0, d1 = x[0] - C_TARGET[0], x[1] - C_TARGET[1]
    return [10.0 * d0 + 9.0 * d1, 9.0 * d0 + 10.0 * d1]


BOUNDS = ([0.0, 0.0], [1.0, 1.0])


# ---------------------------------------------------------------------------
# 线搜索单元测试
# ---------------------------------------------------------------------------

class TestLineSearchUnits(unittest.TestCase):
    """两种线搜索在一维切片上的行为。"""

    def _phi(self, alpha):          # 沿 x0=(1,1), d=-g 的二次切片
        x0, d = [1.0, 1.0], [-1.0, -100.0]
        return quad_f([x0[0] + alpha * d[0], x0[1] + alpha * d[1]])

    def test_armijo_condition_holds(self):
        phi0, dphi0 = self._phi(0.0), -(1.0 + 100.0 ** 2)
        for ls in (backtracking_line_search, interpolation_line_search):
            with self.subTest(ls=ls.__name__):
                res = ls(self._phi, phi0, dphi0, alpha0=1.0)
                self.assertTrue(res.converged)
                self.assertLessEqual(res.f_new, phi0 + 1e-4 * res.alpha * dphi0)
                self.assertLess(res.f_new, phi0)   # 充分下降 => 严格下降

    def test_non_descent_direction_rejected(self):
        for ls in (backtracking_line_search, interpolation_line_search):
            with self.subTest(ls=ls.__name__):
                with self.assertRaises(ValueError):
                    ls(self._phi, self._phi(0.0), dphi0=+1.0)

    def test_huge_alpha0_recovers(self):
        """初始步长 1e8（稳定步长约 1e-4）时两者都必须恢复。"""
        phi0, dphi0 = self._phi(0.0), -(1.0 + 100.0 ** 2)
        for ls in (backtracking_line_search, interpolation_line_search):
            with self.subTest(ls=ls.__name__):
                res = ls(self._phi, phi0, dphi0, alpha0=1e8)
                self.assertTrue(res.converged)
                self.assertLess(res.f_new, phi0)

    def test_interpolation_not_more_evals_than_backtracking(self):
        """二次切片上插值应显著少于回溯的评估次数（初始步长过大时）。"""
        phi0, dphi0 = self._phi(0.0), -(1.0 + 100.0 ** 2)
        rb = backtracking_line_search(self._phi, phi0, dphi0, alpha0=1e8)
        ri = interpolation_line_search(self._phi, phi0, dphi0, alpha0=1e8)
        self.assertLess(ri.n_fevals, rb.n_fevals)

    def test_failure_path_terminates(self):
        """alpha_min 设大、强制失败：必须有限步返回 converged=False。"""
        # phi 单调递增，Armijo 永不成立，步长必收缩到 alpha_min 以下
        phi0, dphi0 = 1.0, -1.0
        phi_up = lambda a: phi0 + a
        for ls in (backtracking_line_search, interpolation_line_search):
            with self.subTest(ls=ls.__name__):
                res = ls(phi_up, phi0, dphi0, alpha0=1.0, alpha_min=1e-8)
                self.assertFalse(res.converged)


class TestMonotoneAssertion(unittest.TestCase):
    def test_accepts_decreasing(self):
        assert_monotone_decreasing([3.0, 2.0, 1.0, 1.0 - 1e-15])

    def test_rejects_increase(self):
        with self.assertRaises(AssertionError):
            assert_monotone_decreasing([1.0, 2.0])


# ---------------------------------------------------------------------------
# 四类情形下的端到端收敛测试
# ---------------------------------------------------------------------------

class TestScenarios(unittest.TestCase):
    def _run(self, f, g, x0, method, **kw):
        res = gradient_descent(f, g, x0, method=method, **kw)
        # 单调下降断言（驱动内部每轮也断言，这里对完整历史再验一次）
        assert_monotone_decreasing(res.f_history)
        for k in range(1, len(res.f_history)):
            self.assertLessEqual(res.f_history[k],
                                 res.f_history[k - 1] + 1e-12)
        return res

    def test_strongly_convex(self):
        for m in METHODS:
            with self.subTest(method=m):
                res = self._run(quad_f, quad_g, [1.0, 1.0], m, tol=1e-10)
                self.assertTrue(res.converged, res.message)
                self.assertAlmostEqual(res.f, 0.0, places=14)

    def test_flat_region(self):
        """四次函数：极小点附近 Hessian 奇异、梯度趋于 0，仍须单调下降并收敛。"""
        for m in METHODS:
            with self.subTest(method=m):
                res = self._run(quartic_f, quartic_g, [1.0, 1.0], m, tol=1e-8)
                self.assertTrue(res.converged, res.message)
                self.assertLess(res.f, 1e-10)

    def test_oversized_initial_step(self):
        """alpha0=1e8：首轮步长严重过大，线搜索必须拉回且全程单调。"""
        for m in METHODS:
            with self.subTest(method=m):
                res = self._run(quad_f, quad_g, [1.0, 1.0], m,
                                alpha0=1e8, tol=1e-10)
                self.assertTrue(res.converged, res.message)
                self.assertAlmostEqual(res.f, 0.0, places=14)

    def test_tiny_initial_step(self):
        """alpha0=1e-8：步长过小靠 alpha_growth 恢复，仍须收敛。"""
        for m in METHODS:
            with self.subTest(method=m):
                res = self._run(quad_f, quad_g, [1.0, 1.0], m,
                                alpha0=1e-8, tol=1e-10)
                self.assertTrue(res.converged, res.message)

    def test_bound_constrained(self):
        """盒约束：解在边界 (1,0) 上，f* = 4，且迭代不越界。"""
        for m in METHODS:
            with self.subTest(method=m):
                res = self._run(bounded_f, bounded_g, [0.2, 0.5], m,
                                bounds=BOUNDS, tol=1e-10)
                self.assertTrue(res.converged, res.message)
                self.assertAlmostEqual(res.x[0], 1.0, places=8)
                self.assertAlmostEqual(res.x[1], 0.0, places=8)
                self.assertAlmostEqual(res.f, 4.0, places=10)
                for xi, lo, hi in zip(res.x, *BOUNDS):
                    self.assertGreaterEqual(xi, lo)
                    self.assertLessEqual(xi, hi)

    def test_boundary_start_on_edge(self):
        """初始点就在边界上且负梯度指向盒外：应原地收敛（KKT 点）。"""
        res = gradient_descent(bounded_f, bounded_g, [1.0, 0.0],
                               method="interpolation",
                               bounds=BOUNDS, tol=1e-10)
        self.assertTrue(res.converged)
        self.assertAlmostEqual(res.f, 4.0, places=12)
        assert_monotone_decreasing(res.f_history)


if __name__ == "__main__":
    unittest.main()
