"""线搜索库自测（标准库 unittest）。

覆盖情形：
  - 强凸函数（病态二次型，条件数 100）
  - 平坦区域（四次函数，极小点附近曲率为 0）
  - 初始步长过大（alpha0 = 1e10）
  - 边界受限（盒式约束 + 投影梯度）
  - Armijo 充分下降条件的逐步验证
  - 非下降方向报错、单调下降断言可触发
"""

import math
import unittest

from linesearch import (
    LineSearchError,
    assert_monotone_decreasing,
    backtracking_line_search,
    gradient_descent,
    interpolation_line_search,
)

METHODS = [("backtracking", backtracking_line_search),
           ("interpolation", interpolation_line_search)]


def quad_strongly_convex():
    """f = 0.5*(x0^2 + 100*x1^2)，x* = (0,0)，f* = 0，条件数 100。"""
    f = lambda x: 0.5 * (x[0] ** 2 + 100.0 * x[1] ** 2)
    g = lambda x: [x[0], 100.0 * x[1]]
    return f, g, [1.0, 1.0], [0.0, 0.0]


def quartic_flat():
    """f = x0^4 + x1^4，极小点处 Hessian 为 0（平坦区域）。"""
    f = lambda x: x[0] ** 4 + x[1] ** 4
    g = lambda x: [4.0 * x[0] ** 3, 4.0 * x[1] ** 3]
    return f, g, [1.0, -1.0], [0.0, 0.0]


def bounded_quad():
    """f = (x0-3)^2 + (x1+2)^2，约束盒 [-1,1]^2，约束最优解 (1,-1)。"""
    f = lambda x: (x[0] - 3.0) ** 2 + (x[1] + 2.0) ** 2
    g = lambda x: [2.0 * (x[0] - 3.0), 2.0 * (x[1] + 2.0)]
    project = lambda x: [min(1.0, max(-1.0, x[0])),
                         min(1.0, max(-1.0, x[1]))]
    return f, g, project, [0.0, 0.0], [1.0, -1.0]


def dist(a, b):
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


class TestStronglyConvex(unittest.TestCase):
    def test_converges(self):
        f, g, x0, xstar = quad_strongly_convex()
        for name, ls in METHODS:
            with self.subTest(method=name):
                x, fx, iters, hist = gradient_descent(f, g, x0, ls)
                self.assertLess(dist(x, xstar), 1e-6)
                self.assertLess(fx, 1e-12)
                assert_monotone_decreasing(hist)
                self.assertGreater(iters, 0)

    def test_every_step_satisfies_armijo(self):
        """线搜索返回的每个点都必须满足 Armijo 充分下降条件。"""
        f, g, x0, _ = quad_strongly_convex()
        c1 = 1e-4
        for name, ls in METHODS:
            with self.subTest(method=name):
                x = list(x0)
                for _ in range(5):
                    gx = g(x)
                    d = [-gi for gi in gx]
                    fx = f(x)
                    alpha, x_new, f_new = ls(f, g, x, d, c1=c1)
                    lhs = f_new
                    rhs = fx + c1 * sum(
                        gi * (xn - xi) for gi, xn, xi in zip(gx, x_new, x))
                    self.assertLessEqual(lhs, rhs + 1e-15)
                    self.assertLess(f_new, fx)  # 严格下降
                    x = x_new


class TestFlatRegion(unittest.TestCase):
    def test_quartic_converges_and_monotone(self):
        f, g, x0, xstar = quartic_flat()
        for name, ls in METHODS:
            with self.subTest(method=name):
                x, fx, iters, hist = gradient_descent(
                    f, g, x0, ls, tol=1e-6, max_iter=50000)
                self.assertLess(dist(x, xstar), 2e-2)
                self.assertLess(fx, 2e-7)
                assert_monotone_decreasing(hist)


class TestHugeInitialStep(unittest.TestCase):
    def test_alpha0_1e10_still_converges(self):
        f, g, x0, xstar = quad_strongly_convex()
        for name, ls in METHODS:
            with self.subTest(method=name):
                x, fx, iters, hist = gradient_descent(
                    f, g, x0, ls, alpha0=1e10)
                self.assertLess(dist(x, xstar), 1e-6)
                assert_monotone_decreasing(hist)

    def test_first_step_is_shrunk(self):
        """alpha0 过大时，实际接受的步长应远小于 alpha0。"""
        f, g, x0, _ = quad_strongly_convex()
        for name, ls in METHODS:
            with self.subTest(method=name):
                d = [-gi for gi in g(x0)]
                alpha, _, _ = ls(f, g, x0, d, alpha0=1e10)
                self.assertLess(alpha, 1.0)


class TestBounded(unittest.TestCase):
    def test_projected_converges_to_boundary(self):
        f, g, project, x0, xstar = bounded_quad()
        for name, ls in METHODS:
            with self.subTest(method=name):
                x, fx, iters, hist = gradient_descent(
                    f, g, x0, ls, project=project, tol=1e-9)
                self.assertLess(dist(x, xstar), 1e-5)
                for xi in x:  # 始终留在可行域内
                    self.assertGreaterEqual(xi, -1.0 - 1e-12)
                    self.assertLessEqual(xi, 1.0 + 1e-12)
                assert_monotone_decreasing(hist)

    def test_boundary_iterates_stay_feasible(self):
        """投影路径上的每一个试探点都在盒内。"""
        f, g, project, x0, _ = bounded_quad()
        for name, ls in METHODS:
            with self.subTest(method=name):
                x = list(x0)
                for _ in range(10):
                    gx = g(x)
                    d = [-gi for gi in gx]
                    _, x, _ = ls(f, g, x, d, project=project)
                    self.assertTrue(all(-1.0 - 1e-12 <= xi <= 1.0 + 1e-12
                                        for xi in x))


class TestRobustness(unittest.TestCase):
    def test_non_descent_direction_raises(self):
        f, g, x0, _ = quad_strongly_convex()
        bad_d = [1.0, 1.0]  # 与梯度同向，是上升方向
        for name, ls in METHODS:
            with self.subTest(method=name):
                with self.assertRaises(LineSearchError):
                    ls(f, g, x0, bad_d)

    def test_monotone_assertion_fires(self):
        with self.assertRaises(AssertionError):
            assert_monotone_decreasing([1.0, 0.5, 0.75])

    def test_line_search_failure_raises(self):
        """alpha_min 设得过大导致无法收缩到位时，应抛出 LineSearchError。"""
        f, g, x0, _ = quad_strongly_convex()
        d = [-gi for gi in g(x0)]
        with self.assertRaises(LineSearchError):
            backtracking_line_search(f, g, x0, d, alpha0=1e10,
                                     alpha_min=1.0, max_iter=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
