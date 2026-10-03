"""三次样条插值自测（仅标准库 unittest）。

运行：
    python3 test_cubic_spline.py            # 静默跑断言
    python3 test_cubic_spline.py -v         # 逐条用例
    python3 test_cubic_spline.py --report   # 额外打印导数连续性对比数据
"""

import math
import sys
import unittest

from cubic_spline import CubicSpline

TOL = 1e-9


def make_data(kind, n):
    """生成测试数据：kind 决定函数与节点分布。"""
    if kind == "uniform":
        x = [i * 1.0 for i in range(n)]
        y = [math.sin(t) for t in x]
    elif kind == "nonuniform":
        x = sorted((i * i * 0.37 + i * 0.11 for i in range(n)))
        y = [math.cos(t) + 0.1 * t for t in x]
    elif kind == "dense":
        x = [i * 0.05 for i in range(n)]
        y = [math.exp(-t) * math.sin(4.0 * t) for t in x]
    else:
        raise ValueError(kind)
    return x, y


def assert_interpolates(testcase, spline, x, y, tol=TOL):
    """逐点误差必须为零（在浮点容差内）。"""
    worst = 0.0
    for xi, yi in zip(x, y):
        err = abs(spline(xi) - yi)
        worst = max(worst, err)
        testcase.assertLessEqual(
            err, tol, f"节点 x={xi} 处插值误差 {err:.3e} 超过容差"
        )
    return worst


def assert_continuous(testcase, spline, tol=1e-6):
    """验证内节点处 S、S'、S'' 左右极限一致，返回对比数据。"""
    rows = spline.continuity_report()
    for row in rows:
        for key in ("S_gap", "S1_gap", "S2_gap"):
            testcase.assertLessEqual(
                row[key],
                tol,
                f"节点 x={row['knot']} 处 {key}={row[key]:.3e} 不连续",
            )
    return rows


class TestInterpolation(unittest.TestCase):
    """插值必须经过每一个已知点。"""

    def test_natural_uniform(self):
        x, y = make_data("uniform", 8)
        s = CubicSpline(x, y, "natural")
        assert_interpolates(self, s, x, y)

    def test_natural_nonuniform(self):
        x, y = make_data("nonuniform", 10)
        s = CubicSpline(x, y, "natural")
        assert_interpolates(self, s, x, y)

    def test_clamped_uniform(self):
        x, y = make_data("uniform", 8)
        s = CubicSpline(x, y, ("clamped", math.cos(x[0]), math.cos(x[-1])))
        assert_interpolates(self, s, x, y)

    def test_clamped_nonuniform(self):
        x, y = make_data("nonuniform", 10)
        d0 = -math.sin(x[0]) + 0.1
        dn = -math.sin(x[-1]) + 0.1
        s = CubicSpline(x, y, ("clamped", d0, dn))
        assert_interpolates(self, s, x, y)


class TestContinuity(unittest.TestCase):
    """一阶与二阶导数在分段连接处连续。"""

    def test_continuity_natural(self):
        x, y = make_data("nonuniform", 12)
        s = CubicSpline(x, y, "natural")
        assert_continuous(self, s)

    def test_continuity_clamped(self):
        x, y = make_data("uniform", 9)
        s = CubicSpline(x, y, ("clamped", 1.0, -0.5))
        assert_continuous(self, s)


class TestBoundaryConditions(unittest.TestCase):
    """边界条件本身必须被满足。"""

    def test_natural_second_derivative_zero(self):
        x, y = make_data("uniform", 7)
        s = CubicSpline(x, y, "natural")
        self.assertAlmostEqual(s.derivative(x[0], 2), 0.0, places=9)
        self.assertAlmostEqual(s.derivative(x[-1], 2), 0.0, places=9)

    def test_clamped_first_derivative(self):
        x, y = make_data("uniform", 7)
        d0, dn = 0.3, -1.2
        s = CubicSpline(x, y, ("clamped", d0, dn))
        self.assertAlmostEqual(s.derivative(x[0], 1), d0, places=9)
        self.assertAlmostEqual(s.derivative(x[-1], 1), dn, places=9)

    def test_missing_bc_defaults_to_natural(self):
        """端点条件缺失时缺省使用自然边界。"""
        x, y = make_data("uniform", 6)
        s = CubicSpline(x, y)  # 不传 bc
        self.assertEqual(s.bc_type, "natural")
        self.assertAlmostEqual(s.derivative(x[0], 2), 0.0, places=9)
        assert_interpolates(self, s, x, y)

    def test_invalid_bc_rejected(self):
        with self.assertRaises(ValueError):
            CubicSpline([0.0, 1.0], [0.0, 1.0], "periodic")


class TestEdgeCases(unittest.TestCase):
    """两个点、等距点、密集点等情形。"""

    def test_two_points_natural(self):
        """两点自然样条退化为直线。"""
        s = CubicSpline([0.0, 2.0], [1.0, 5.0], "natural")
        assert_interpolates(self, s, [0.0, 2.0], [1.0, 5.0])
        self.assertAlmostEqual(s(1.0), 3.0, places=9)
        self.assertAlmostEqual(s.derivative(1.0, 1), 2.0, places=9)

    def test_two_points_clamped(self):
        """两点 + 端点导数：退化为三次 Hermite 插值。"""
        s = CubicSpline([0.0, 1.0], [0.0, 1.0], ("clamped", 0.0, 0.0))
        assert_interpolates(self, s, [0.0, 1.0], [0.0, 1.0])
        self.assertAlmostEqual(s.derivative(0.0, 1), 0.0, places=9)
        self.assertAlmostEqual(s.derivative(1.0, 1), 0.0, places=9)
        self.assertAlmostEqual(s(0.5), 0.5, places=9)  # 光滑台阶曲线中点

    def test_equally_spaced(self):
        x, y = make_data("uniform", 15)
        for bc in ("natural", ("clamped", 1.0, 1.0)):
            s = CubicSpline(x, y, bc)
            assert_interpolates(self, s, x, y)
            assert_continuous(self, s)

    def test_dense_points(self):
        """密集节点（200 个）下依然稳定、逐点过点且导数连续。"""
        x, y = make_data("dense", 200)
        s = CubicSpline(x, y, "natural")
        assert_interpolates(self, s, x, y, tol=1e-8)
        assert_continuous(self, s, tol=1e-5)

    def test_single_point_rejected(self):
        with self.assertRaises(ValueError):
            CubicSpline([0.0], [0.0])

    def test_non_increasing_x_rejected(self):
        with self.assertRaises(ValueError):
            CubicSpline([0.0, 0.0, 1.0], [0.0, 1.0, 2.0])


class TestLinearSystem(unittest.TestCase):
    """系数求解：方程组构造与解的自洽性。"""

    def test_system_residual(self):
        """解出的 M 代回 A M = rhs，残差必须为零。"""
        x, y = make_data("nonuniform", 9)
        for bc in ("natural", ("clamped", 0.2, -0.4)):
            s = CubicSpline(x, y, bc)
            n = s.n
            for i in range(n):
                lhs = sum(s.A[i][j] * s.M[j] for j in range(n))
                self.assertAlmostEqual(lhs, s.rhs[i], places=7,
                                       msg=f"bc={bc} 第 {i} 行残差非零")

    def test_exact_cubic_reproduction(self):
        """对三次多项式采样，clamped 样条应精确还原该多项式。"""
        f = lambda t: t ** 3 - 2.0 * t ** 2 + 0.5 * t - 1.0
        df = lambda t: 3.0 * t ** 2 - 4.0 * t + 0.5
        x = [0.0, 0.7, 1.3, 2.6, 4.0]
        y = [f(t) for t in x]
        s = CubicSpline(x, y, ("clamped", df(x[0]), df(x[-1])))
        for k in range(41):
            t = 4.0 * k / 40.0
            self.assertAlmostEqual(s(t), f(t), places=7)


def print_continuity_report():
    """打印分段连接处导数对比数据（--report 模式）。"""
    datasets = [
        ("等距点 natural (sin)", *make_data("uniform", 8), "natural"),
        ("非等距点 clamped (cos+0.1x)", *make_data("nonuniform", 8),
         ("clamped", 0.1 - math.sin(0.0), 0.0)),
        ("密集点 natural (e^-t sin4t)", *make_data("dense", 60), "natural"),
    ]
    for name, x, y, bc in datasets:
        if isinstance(bc, tuple):  # 用真实端点导数
            bc = ("clamped", -math.sin(x[0]) + 0.1, -math.sin(x[-1]) + 0.1)
        s = CubicSpline(x, y, bc)
        print(f"\n=== {name}  bc={s.bc_type} ===")
        print(f"{'knot x':>10} | {'S1 左':>14} {'S1 右':>14} {'|gap|':>10} | "
              f"{'S2 左':>14} {'S2 右':>14} {'|gap|':>10}")
        rows = s.continuity_report()
        shown = rows if len(rows) <= 10 else rows[:5] + rows[-5:]
        for r in shown:
            print(f"{r['knot']:>10.4f} | "
                  f"{r['S1_left']:>14.8f} {r['S1_right']:>14.8f} "
                  f"{r['S1_gap']:>10.2e} | "
                  f"{r['S2_left']:>14.8f} {r['S2_right']:>14.8f} "
                  f"{r['S2_gap']:>10.2e}")
        if len(rows) > 10:
            print(f"  ... 共 {len(rows)} 个内节点，仅显示首尾各 5 个 ...")
        worst1 = max(r["S1_gap"] for r in rows)
        worst2 = max(r["S2_gap"] for r in rows)
        print(f"最大 S' 跳变 {worst1:.2e}，最大 S'' 跳变 {worst2:.2e}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.argv.remove("--report")
        print_continuity_report()
        print()
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
