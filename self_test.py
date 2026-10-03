"""自测：python3 self_test.py  （或 python3 -m unittest self_test -v）

覆盖：
1. 放大因子理论与实测一致（谱验证）
2. 临界步长两侧稳定性翻转（纯对流/纯扩散/耦合）
3. 隐式格式无条件稳定
4. 边界条件（周期/Dirichlet/Neumann）与精确解对比
5. 三对角求解器（含周期 Sherman-Morrison）正确性
"""

import math
import unittest

from fdstab.schemes import evolve, thomas, cyclic_thomas
from fdstab.von_neumann import critical_dt, max_growth
from fdstab.experiments import spectral_check, growth_experiment

A, NU = 1.0, 0.01
DX = 0.005


class TestSpectral(unittest.TestCase):
    """单谐波实测放大因子应贴近理论 |G|。"""

    def _check(self, scheme, a, nu, dt, tol=2e-3):
        res = spectral_check(scheme, a, nu, DX, dt, k_wave=1, n=200, steps=5)
        self.assertAlmostEqual(res["measured"], res["theory"], delta=tol,
                               msg="%s: measured=%.6f theory=%.6f"
                               % (scheme, res["measured"], res["theory"]))

    def test_ftcs(self):
        self._check("ftcs", A, NU, 0.9 * critical_dt("ftcs", DX, A, NU)[0])

    def test_upwind(self):
        self._check("upwind", A, NU, 0.9 * critical_dt("upwind", DX, A, NU)[0])

    def test_lax_friedrichs(self):
        self._check("lax_friedrichs", A, 0.0, 0.9 * DX / A)

    def test_lax_wendroff(self):
        self._check("lax_wendroff", A, 0.0, 0.9 * DX / A)

    def test_btcs(self):
        self._check("btcs", A, NU, 5.0 * DX / A)

    def test_crank_nicolson(self):
        self._check("crank_nicolson", A, NU, 5.0 * DX / A)


class TestCriticalPoint(unittest.TestCase):
    """临界步长两侧：略小稳定、略大失稳。"""

    def _flip(self, scheme, a, nu, dt_crit, steps=40):
        below = growth_experiment(scheme, a, nu, DX, 0.98 * dt_crit, steps=steps)
        above = growth_experiment(scheme, a, nu, DX, 1.02 * dt_crit, steps=steps)
        self.assertTrue(below["stable_flag"],
                        "%s 0.98*dt_crit 应稳定, growth=%.3g" % (scheme, below["growth"]))
        self.assertFalse(above["stable_flag"],
                         "%s 1.02*dt_crit 应失稳, growth=%.3g" % (scheme, above["growth"]))

    def test_pure_diffusion_ftcs(self):
        self._flip("ftcs", 0.0, NU, critical_dt("ftcs", DX, 0.0, NU)[0])

    def test_pure_advection_upwind(self):
        self._flip("upwind", A, 0.0, critical_dt("upwind", DX, A, 0.0)[0])

    def test_pure_advection_lax_friedrichs(self):
        self._flip("lax_friedrichs", A, 0.0, DX / A)

    def test_pure_advection_lax_wendroff(self):
        self._flip("lax_wendroff", A, 0.0, DX / A)

    def test_coupled_ftcs(self):
        self._flip("ftcs", A, NU, critical_dt("ftcs", DX, A, NU)[0])

    def test_coupled_upwind(self):
        self._flip("upwind", A, NU, critical_dt("upwind", DX, A, NU)[0])

    def test_pure_advection_ftcs_always_unstable(self):
        dt_crit, _ = critical_dt("ftcs", DX, A, 0.0)
        self.assertEqual(dt_crit, 0.0)
        res = growth_experiment("ftcs", A, 0.0, DX, 0.1 * DX / A, steps=40)
        self.assertFalse(res["stable_flag"])


class TestImplicit(unittest.TestCase):
    """隐式格式无条件稳定：取远超显式临界的 dt 仍不发散。"""

    def test_btcs_large_dt(self):
        dt = 20.0 * critical_dt("ftcs", DX, A, NU)[0]
        res = growth_experiment("btcs", A, NU, DX, dt, steps=20)
        self.assertTrue(res["stable_flag"], "growth=%.3g" % res["growth"])

    def test_crank_nicolson_large_dt(self):
        dt = 20.0 * critical_dt("ftcs", DX, A, NU)[0]
        res = growth_experiment("crank_nicolson", A, NU, DX, dt, steps=20)
        self.assertTrue(res["stable_flag"], "growth=%.3g" % res["growth"])

    def test_max_growth_le_one(self):
        for scheme in ("btcs", "crank_nicolson"):
            gmax, _ = max_growth(scheme, 3.0, 2.0)
            self.assertLessEqual(gmax, 1.0 + 1e-12)


class TestBoundaries(unittest.TestCase):
    """边界用例与精确解对比。"""

    def test_dirichlet_diffusion(self):
        # u_t = nu u_xx, u(0)=u(1)=0, u0=sin(pi x) -> exp(-nu pi^2 t) sin(pi x)
        n, nu, t_end = 99, 0.05, 0.2
        dx = 1.0 / (n + 1)
        dt = 0.4 * dx * dx / (2.0 * nu)
        nsteps = int(round(t_end / dt))
        xs = [(j + 1) * dx for j in range(n)]
        u0 = [math.sin(math.pi * x) for x in xs]
        u = evolve("ftcs", u0, dx, dt, 0.0, nu, nsteps,
                   bc="dirichlet", bc_left=0.0, bc_right=0.0)
        t = nsteps * dt
        err = max(abs(u[j] - math.exp(-nu * math.pi ** 2 * t) * math.sin(math.pi * xs[j]))
                  for j in range(n))
        self.assertLess(err, 5e-3)

    def test_neumann_diffusion(self):
        # u_x=0 两端, u0=cos(pi x) -> exp(-nu pi^2 t) cos(pi x)
        n, nu, t_end = 99, 0.05, 0.2
        dx = 1.0 / (n + 1)
        dt = 0.4 * dx * dx / (2.0 * nu)
        nsteps = int(round(t_end / dt))
        xs = [(j + 1) * dx for j in range(n)]
        u0 = [math.cos(math.pi * x) for x in xs]
        u = evolve("btcs", u0, dx, dt, 0.0, nu, nsteps,
                   bc="neumann", bc_left=0.0, bc_right=0.0)
        t = nsteps * dt
        err = max(abs(u[j] - math.exp(-nu * math.pi ** 2 * t) * math.cos(math.pi * xs[j]))
                  for j in range(n))
        self.assertLess(err, 2e-2)  # 边界处理一阶，容差放宽

    def test_periodic_advection(self):
        # u_t + a u_x = 0 周期, u0=sin(2 pi x) -> sin(2 pi (x - a t))
        n, a, t_end = 199, 1.0, 0.3
        dx = 1.0 / (n + 1)
        dt = 0.8 * dx / a
        nsteps = int(round(t_end / dt))
        xs = [(j + 1) * dx for j in range(n)]
        u0 = [math.sin(2.0 * math.pi * x) for x in xs]
        u = evolve("upwind", u0, dx, dt, a, 0.0, nsteps, bc="periodic")
        t = nsteps * dt
        err = max(abs(u[j] - math.sin(2.0 * math.pi * (xs[j] - a * t)))
                  for j in range(n))
        self.assertLess(err, 0.35)  # 迎风一阶，数值扩散明显，仅验证无失稳与相位大致正确

    def test_time_dependent_dirichlet(self):
        # 边界随时间变化不发散
        n, nu = 99, 0.05
        dx = 1.0 / (n + 1)
        dt = 0.4 * dx * dx / (2.0 * nu)
        u0 = [0.0] * n
        u = evolve("btcs", u0, dx, dt, 0.0, nu, 200,
                   bc="dirichlet", bc_left=lambda t: math.sin(t), bc_right=0.0)
        self.assertTrue(all(math.isfinite(v) and abs(v) < 10.0 for v in u))


class TestSolvers(unittest.TestCase):
    def test_thomas(self):
        low = [0.0, -1.0, -1.0]
        diag = [2.0, 2.0, 2.0]
        up = [-1.0, -1.0, 0.0]
        rhs = [1.0, 0.0, 1.0]
        x = thomas(low, diag, up, rhs)
        for i in range(3):
            lhs = diag[i] * x[i]
            if i > 0:
                lhs += low[i] * x[i - 1]
            if i < 2:
                lhs += up[i] * x[i + 1]
            self.assertAlmostEqual(lhs, rhs[i], places=12)

    def test_cyclic_thomas(self):
        n = 8
        low = [-0.3] * n
        diag = [1.6] * n
        up = [-0.2] * n
        wrap_l, wrap_u = -0.3, -0.2
        rhs = [float(i + 1) for i in range(n)]
        x = cyclic_thomas(low, diag, up, rhs, wrap_l, wrap_u)
        for i in range(n):
            lhs = diag[i] * x[i] + low[i] * x[(i - 1) % n] + up[i] * x[(i + 1) % n]
            self.assertAlmostEqual(lhs, rhs[i], places=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
