"""Unit tests for adaptive_integrator (standard library only).

Run:  python3 test_adaptive_integrator.py   (or: python3 -m unittest -v)
"""

import math
import unittest

from adaptive_integrator import (
    EPS,
    IntegrateResult,
    _BS23,
    _RKF45,
    _embedded_step,
    integrate,
)


def exp_rhs(t, y):
    return [-y[0]]


def osc_rhs(t, y):
    return [y[1], -y[0]]


class TestAccuracy(unittest.TestCase):
    def test_smooth_exponential(self):
        tol = 1e-8
        r = integrate(exp_rhs, 0.0, [1.0], 5.0, rtol=tol, atol=tol * 0.01)
        self.assertEqual(r.status, "success")
        self.assertLess(abs(r.y[0] - math.exp(-5.0)), 50.0 * tol)

    def test_oscillator_system(self):
        tol = 1e-9
        r = integrate(osc_rhs, 0.0, [1.0, 0.0], 20.0, rtol=tol, atol=tol * 0.01)
        self.assertEqual(r.status, "success")
        err = max(abs(r.y[0] - math.cos(20.0)), abs(r.y[1] + math.sin(20.0)))
        self.assertLess(err, 50.0 * tol)

    def test_bs23_pair(self):
        tol = 1e-6
        r = integrate(exp_rhs, 0.0, [1.0], 5.0, rtol=tol, atol=tol * 0.01,
                      method="bs23")
        self.assertEqual(r.status, "success")
        self.assertLess(abs(r.y[0] - math.exp(-5.0)), 50.0 * tol)

    def test_scalar_initial_state_accepted(self):
        r = integrate(exp_rhs, 0.0, 1.0, 1.0, rtol=1e-6, atol=1e-9)
        self.assertEqual(r.status, "success")
        self.assertAlmostEqual(r.y[0], math.exp(-1.0), places=6)

    def test_backward_integration(self):
        r = integrate(exp_rhs, 5.0, [math.exp(-5.0)], 0.0, rtol=1e-8, atol=1e-11)
        self.assertEqual(r.status, "success")
        self.assertLess(abs(r.y[0] - 1.0), 1e-7)


class TestEmbeddedPairs(unittest.TestCase):
    """The two formulas of each pair must differ by exactly one order."""

    def test_rkf45_orders(self):
        f = exp_rhs
        errs = []
        for h in (0.25, 0.125):
            y_hi, err, _, _ = _embedded_step(f, 0.0, [1.0], [-1.0], h, _RKF45)
            e5 = abs(y_hi[0] - math.exp(-h))
            e4 = abs(y_hi[0] - err[0] - math.exp(-h))
            errs.append((e4, e5))
        # 5th-order solution is the more accurate one
        self.assertGreater(errs[1][0], 5.0 * errs[1][1])
        # the accuracy ratio grows ~2x when h is halved (one order apart)
        ratio_growth = (errs[1][0] / errs[1][1]) / (errs[0][0] / errs[0][1])
        self.assertTrue(1.5 < ratio_growth < 2.5, ratio_growth)

    def test_bs23_orders(self):
        y_hi, err, _, _ = _embedded_step(exp_rhs, 0.0, [1.0], [-1.0], 0.125, _BS23)
        e3 = abs(y_hi[0] - math.exp(-0.125))
        e2 = abs(y_hi[0] - err[0] - math.exp(-0.125))
        self.assertGreater(e2, e3)


class TestStepSizeControl(unittest.TestCase):
    def test_tolerance_changes_step_count(self):
        counts = []
        for tol in (1e-4, 1e-6, 1e-8, 1e-10):
            r = integrate(exp_rhs, 0.0, [1.0], 5.0, rtol=tol, atol=tol * 0.01)
            self.assertEqual(r.status, "success")
            counts.append(r.n_accepted)
        self.assertTrue(all(a < b for a, b in zip(counts, counts[1:])),
                        "step count must grow as tolerance tightens: %s" % counts)

    def test_rejections_counted_on_steep_problem(self):
        # Logistic with a steep transient: y jumps 1e-4 -> ~1 near t ~ 9.2
        r = integrate(lambda t, y: [y[0] * (1.0 - y[0])], 0.0, [1e-4], 20.0,
                      rtol=1e-9, atol=1e-12)
        self.assertEqual(r.status, "success")
        self.assertGreater(r.n_rejected, 0)
        self.assertLess(r.rejection_ratio, 0.5)
        self.assertEqual(r.n_steps, r.n_accepted + r.n_rejected)

    def test_lands_exactly_on_t1(self):
        t1 = 1.23456789
        r = integrate(exp_rhs, 0.0, [1.0], t1, rtol=1e-8, atol=1e-10)
        self.assertEqual(r.status, "success")
        self.assertEqual(r.t, t1)

    def test_h_max_respected(self):
        r = integrate(exp_rhs, 0.0, [1.0], 5.0, rtol=1e-6, atol=1e-9, h_max=0.01)
        self.assertEqual(r.status, "success")
        self.assertGreaterEqual(r.n_accepted, 500)


class TestMinStepProtection(unittest.TestCase):
    def test_min_step_triggers_on_singularity(self):
        # y' = y^2, y(0)=1 blows up at t=1; integration must stop, not hang.
        r = integrate(lambda t, y: [y[0] ** 2], 0.0, [1.0], 2.0,
                      rtol=1e-8, atol=1e-10)
        self.assertEqual(r.status, "min_step")
        self.assertGreater(r.t, 0.99)
        self.assertLess(r.t, 1.0)
        self.assertIn("h_min", r.message)

    def test_min_step_default_criterion(self):
        # Default h_min = 100 * eps * max(|t0|, |t1|, 1)
        r = integrate(lambda t, y: [y[0] ** 2], 0.0, [1.0], 2.0,
                      rtol=1e-8, atol=1e-10)
        self.assertEqual(r.status, "min_step")
        floor = 100.0 * EPS * 2.0
        self.assertIn("%.3e" % floor, r.message)

    def test_force_mode_counts_events(self):
        r = integrate(lambda t, y: [y[0] ** 2], 0.0, [1.0], 2.0,
                      rtol=1e-8, atol=1e-10, on_min_step="force")
        self.assertGreater(r.min_step_events, 0)
        self.assertEqual(r.status, "diverged")  # y -> inf shortly after t=1

    def test_too_strict_tolerance_with_user_floor(self):
        # Demanding 1e-17 relative on a fast oscillator needs h ~ 1e-4;
        # a user floor of 1e-3 must trigger the min-step stop at t=0.
        f = lambda t, y: [y[1], -100.0 * y[0]]
        r = integrate(f, 0.0, [1.0, 0.0], 20.0, rtol=1e-17, atol=0.0,
                      h_min=1e-3)
        self.assertEqual(r.status, "min_step")
        self.assertEqual(r.t, 0.0)

    def test_force_mode_completes_with_violated_tolerance(self):
        f = lambda t, y: [y[1], -100.0 * y[0]]
        r = integrate(f, 0.0, [1.0, 0.0], 20.0, rtol=1e-17, atol=0.0,
                      h_min=1e-3, on_min_step="force")
        self.assertEqual(r.status, "success")
        self.assertGreater(r.min_step_events, 1000)

    def test_h_min_clamped_to_roundoff_floor(self):
        # A user h_min below the float64 clock resolution is clamped up.
        r = integrate(exp_rhs, 0.0, [1.0], 5.0, rtol=1e-8, atol=1e-10,
                      h_min=1e-300)
        self.assertEqual(r.status, "success")


class TestSafetyGuards(unittest.TestCase):
    def test_max_steps_guard(self):
        r = integrate(exp_rhs, 0.0, [1.0], 100.0, rtol=1e-12, atol=0.0,
                      max_steps=100)
        self.assertEqual(r.status, "max_steps")
        self.assertLess(r.t, 100.0)

    def test_diverged_rhs_overflow(self):
        # y' = y^3 overflows almost immediately from y0=1e100
        r = integrate(lambda t, y: [y[0] ** 3], 0.0, [1e100], 1.0,
                      rtol=1e-6, atol=1e-9)
        self.assertIn(r.status, ("diverged", "min_step"))

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            integrate(exp_rhs, 0.0, [1.0], 1.0, method="euler")
        with self.assertRaises(ValueError):
            integrate(exp_rhs, 0.0, [1.0], 1.0, rtol=0.0, atol=0.0)
        with self.assertRaises(ValueError):
            integrate(exp_rhs, 0.0, [1.0], 1.0, on_min_step="ignore")

    def test_empty_interval(self):
        r = integrate(exp_rhs, 1.0, [0.5], 1.0)
        self.assertEqual(r.status, "success")
        self.assertEqual(r.y, [0.5])


if __name__ == "__main__":
    unittest.main(verbosity=2)
