"""稳定性分析库自测（标准库 unittest）。

运行: python3 -m unittest discover -s tests -v
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from stability_analysis import von_neumann as vn
from stability_analysis import solvers as sv
from stability_analysis import exact


class TestLinearAlgebra(unittest.TestCase):
    def test_thomas_known_solution(self):
        # 构造已知解 x = [1,2,3,4], 反推 rhs
        lower = [0.0, 1.0, 2.0, 3.0]
        diag = [4.0, 5.0, 6.0, 7.0]
        upper = [1.0, 2.0, 3.0, 0.0]
        x_true = [1.0, 2.0, 3.0, 4.0]
        n = 4
        rhs = []
        for i in range(n):
            v = diag[i] * x_true[i]
            if i > 0:
                v += lower[i] * x_true[i - 1]
            if i < n - 1:
                v += upper[i] * x_true[i + 1]
            rhs.append(v)
        x = sv.thomas(lower, diag, upper, rhs)
        for xi, xt in zip(x, x_true):
            self.assertAlmostEqual(xi, xt, places=12)

    def test_cyclic_thomas_residual(self):
        n = 8
        lower = [0.0] + [-1.5] * (n - 1)
        diag = [4.0] * n
        upper = [-0.5] * (n - 1) + [0.0]
        corner_lower, corner_upper = -1.5, -0.5
        rhs = [float(i % 3) - 0.5 for i in range(n)]
        x = sv.cyclic_thomas(lower, diag, upper, rhs,
                             corner_lower, corner_upper)
        # 验证残差 A x - rhs ~ 0
        for i in range(n):
            v = diag[i] * x[i]
            if i > 0:
                v += lower[i] * x[i - 1]
            else:
                v += corner_upper * x[n - 1]
            if i < n - 1:
                v += upper[i] * x[i + 1]
            else:
                v += corner_lower * x[0]
            self.assertAlmostEqual(v, rhs[i], places=10)


class TestVonNeumann(unittest.TestCase):
    a, nu, dx = 1.0, 0.01, 0.01

    def _crosses_at(self, scan, dtc):
        below = scan(0.99 * dtc)
        above = scan(1.01 * dtc)
        self.assertLessEqual(below, 1.0 + 1e-12)
        self.assertGreater(above, 1.0)

    def test_upwind_advection_boundary(self):
        a, dx = self.a, self.dx
        self._crosses_at(
            lambda dt: vn.max_amplification(
                lambda th: vn.g_upwind_advection(th, a * dt / dx)),
            vn.critical_dt_upwind(a, dx))

    def test_diffusion_boundary(self):
        nu, dx = self.nu, self.dx
        self._crosses_at(
            lambda dt: vn.max_amplification(
                lambda th: vn.g_ftcs_diffusion(th, nu * dt / dx ** 2)),
            vn.critical_dt_diffusion(nu, dx))

    def test_upwind_adv_diff_boundary(self):
        a, nu, dx = self.a, self.nu, self.dx
        self._crosses_at(
            lambda dt: vn.max_amplification(
                lambda th: vn.g_upwind_adv_diff(
                    th, a * dt / dx, nu * dt / dx ** 2)),
            vn.critical_dt_upwind_adv_diff(a, nu, dx))

    def test_central_adv_diff_boundary(self):
        a, nu, dx = self.a, self.nu, self.dx
        self._crosses_at(
            lambda dt: vn.max_amplification(
                lambda th: vn.g_central_adv_diff(
                    th, a * dt / dx, nu * dt / dx ** 2)),
            vn.critical_dt_central_adv_diff(a, nu, dx))

    def test_ftcs_advection_always_unstable(self):
        for c in (0.01, 0.1, 0.5, 0.99):
            g = vn.max_amplification(lambda th: vn.g_ftcs_advection(th, c))
            self.assertGreater(g, 1.0)

    def test_implicit_unconditionally_stable(self):
        a, nu, dx = self.a, self.nu, self.dx
        dtc = vn.critical_dt_upwind_adv_diff(a, nu, dx)
        for mult in (1.0, 10.0, 100.0, 1000.0):
            dt = mult * dtc
            g = vn.max_amplification(
                lambda th: vn.g_implicit_upwind_adv_diff(
                    th, a * dt / dx, nu * dt / dx ** 2))
            self.assertLessEqual(g, 1.0 + 1e-12)

    def test_critical_dt_by_bisection(self):
        """用二分法数值求 max|G|=1 的 dt, 与解析公式对比。"""
        a, nu, dx = self.a, self.nu, self.dx
        # 迎风对流
        def scan_adv(dt):
            return vn.max_amplification(
                lambda th: vn.g_upwind_advection(th, a * dt / dx))
        lo, hi = 0.0, 10.0 * vn.critical_dt_upwind(a, dx)
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if scan_adv(mid) <= 1.0:
                lo = mid
            else:
                hi = mid
        self.assertAlmostEqual(lo, vn.critical_dt_upwind(a, dx),
                               delta=1e-6 * vn.critical_dt_upwind(a, dx))
        # 对流扩散
        def scan_ad(dt):
            return vn.max_amplification(
                lambda th: vn.g_upwind_adv_diff(
                    th, a * dt / dx, nu * dt / dx ** 2))
        lo, hi = 0.0, 10.0 * vn.critical_dt_upwind_adv_diff(a, nu, dx)
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if scan_ad(mid) <= 1.0:
                lo = mid
            else:
                hi = mid
        self.assertAlmostEqual(
            lo, vn.critical_dt_upwind_adv_diff(a, nu, dx),
            delta=1e-6 * vn.critical_dt_upwind_adv_diff(a, nu, dx))


class TestSchemes(unittest.TestCase):
    def test_upwind_unit_cfl_exact(self):
        """c=1 时迎风格式每步精确平移一格。"""
        L, N, a = 1.0, 64, 1.0
        dx = L / N
        dt = dx / a
        u0 = [exact.advected_sine(i * dx, 0.0, a, L) for i in range(N)]
        u, _ = sv.explicit_solve(u0, dx, dt, 10, a, 0.0, bc='periodic')
        ue = [exact.advected_sine(i * dx, 10 * dt, a, L) for i in range(N)]
        self.assertLess(sv.l2_error(u, ue, dx), 1e-12)

    def test_diffusion_stable_below_critical(self):
        L, N, nu = 1.0, 50, 0.01
        dx = L / N
        dt = 0.9 * vn.critical_dt_diffusion(nu, dx)
        u0 = [exact.decaying_sine(i * dx, 0.0, nu, L) for i in range(N)]
        u, _ = sv.explicit_solve(u0, dx, dt, 100, 0.0, nu, bc='periodic')
        ue = [exact.decaying_sine(i * dx, 100 * dt, nu, L) for i in range(N)]
        self.assertLess(sv.l2_error(u, ue, dx), 1e-3)

    def test_diffusion_unstable_above_critical(self):
        L, N, nu = 1.0, 50, 0.01
        dx = L / N
        dt = 1.1 * vn.critical_dt_diffusion(nu, dx)
        pert = [1e-6 * ((-1.0) ** i) for i in range(N)]
        u, _ = sv.explicit_solve(pert, dx, dt, 100, 0.0, nu, bc='periodic')
        self.assertGreater(sv.l2_norm(u, dx), 1.0)  # 扰动被放大 1e6 倍以上

    def test_implicit_large_dt_bounded(self):
        L, N, nu = 1.0, 50, 0.01
        dx = L / N
        dt = 50.0 * vn.critical_dt_diffusion(nu, dx)
        u0 = [exact.decaying_sine(i * dx, 0.0, nu, L) for i in range(N)]
        u, _ = sv.implicit_solve(u0, dx, dt, 20, 0.0, nu, bc='periodic')
        self.assertLess(max(abs(v) for v in u), 1.0)

    def test_advection_upwind_stable_below_unstable_above(self):
        L, N, a = 1.0, 50, 1.0
        dx = L / N
        dtc = vn.critical_dt_upwind(a, dx)
        pert = [1e-6 * ((-1.0) ** i) for i in range(N)]
        u, _ = sv.explicit_solve(pert, dx, 0.9 * dtc, 100, a, 0.0,
                                 bc='periodic')
        self.assertLess(sv.l2_norm(u, dx), 1e-6)
        u, _ = sv.explicit_solve(pert, dx, 1.1 * dtc, 100, a, 0.0,
                                 bc='periodic')
        self.assertGreater(sv.l2_norm(u, dx), 1.0)


class TestBoundaryConditions(unittest.TestCase):
    def test_dirichlet_inflow_matches_exact(self):
        L, N, a = 1.0, 200, 1.0
        dx = L / N
        dt = 0.5 * dx / a
        T = 0.5
        steps = int(round(T / dt))
        g = lambda t: math.sin(2.0 * math.pi * t)
        u0 = [0.0] * (N + 1)
        u, _ = sv.explicit_solve(u0, dx, dt, steps, a, 0.0,
                                 bc='dirichlet',
                                 g_left=g, g_right=lambda t: 0.0)
        n_valid = int(0.9 * a * T / dx)
        ue = [g(T - i * dx / a) for i in range(n_valid)]
        self.assertLess(sv.l2_error(u[:n_valid], ue, dx), 0.02)
        self.assertLess(max(abs(v) for v in u), 2.0)

    def test_outflow_bounded(self):
        L, N, a = 1.0, 200, 1.0
        dx = L / N
        dt = 0.5 * dx / a
        u0 = [math.sin(math.pi * i * dx / L) for i in range(N + 1)]
        u, _ = sv.explicit_solve(u0, dx, dt, int(0.8 / dt), a, 0.0,
                                 bc='outflow', g_left=lambda t: 0.0)
        self.assertLess(max(abs(v) for v in u), 1.0 + 1e-9)

    def test_neumann_conserves_mass(self):
        L, N, nu = 1.0, 100, 0.05
        dx = L / N
        dt = 0.4 * vn.critical_dt_diffusion(nu, dx)
        u0 = [1.0 + 0.5 * math.cos(math.pi * i * dx / L)
              for i in range(N + 1)]
        u, _ = sv.explicit_solve(u0, dx, dt, 200, 0.0, nu, bc='neumann')
        self.assertAlmostEqual(dx * sum(u), dx * sum(u0), places=9)

    def test_dirichlet_adv_diff_blows_up_above_critical(self):
        L, N = 1.0, 200
        a, nu = 1.0, 0.01
        dx = L / N
        dtc = vn.critical_dt_upwind_adv_diff(a, nu, dx)
        u0 = [0.0] * (N + 1)
        u, _ = sv.explicit_solve(u0, dx, 0.9 * dtc, 200, a, nu,
                                 bc='dirichlet',
                                 g_left=lambda t: 1.0,
                                 g_right=lambda t: 0.0)
        self.assertLess(max(abs(v) for v in u), 10.0)
        u, _ = sv.explicit_solve(u0, dx, 1.1 * dtc, 200, a, nu,
                                 bc='dirichlet',
                                 g_left=lambda t: 1.0,
                                 g_right=lambda t: 0.0)
        self.assertGreater(max(abs(v) for v in u), 10.0)

    def test_implicit_dirichlet(self):
        L, N, nu = 1.0, 50, 0.05
        dx = L / N
        dt = 10.0 * vn.critical_dt_diffusion(nu, dx)
        u0 = [0.0] * (N + 1)
        u, _ = sv.implicit_solve(u0, dx, dt, 50, 0.0, nu,
                                 bc='dirichlet',
                                 g_left=lambda t: 1.0,
                                 g_right=lambda t: 0.0)
        self.assertAlmostEqual(u[0], 1.0, places=12)
        self.assertAlmostEqual(u[-1], 0.0, places=12)
        self.assertLess(max(abs(v) for v in u), 1.0 + 1e-9)


if __name__ == '__main__':
    unittest.main()
