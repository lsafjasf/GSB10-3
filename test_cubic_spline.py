"""
Self-tests for cubic_spline.py (standard library only).

Run:  python3 test_cubic_spline.py

Covers:
  * interpolation hits every knot (per-point zero-error assertions)
  * C1/C2 continuity at interior knots (with printed comparison data)
  * two points, equally spaced points, dense points
  * missing endpoint conditions (default natural), mixed boundary
  * natural vs clamped boundary producing visibly different curves
  * error handling for bad input
"""

import math
import random

from cubic_spline import CubicSpline

TOL_KNOT = 1e-9    # interpolation error at knots
TOL_CONT = 1e-7    # C1/C2 continuity at interior knots
TOL_BC = 1e-9      # boundary condition satisfaction


# --------------------------------------------------------------------- #
# Assertions shared by all scenarios
# --------------------------------------------------------------------- #
def assert_interpolation(spline, xs, ys, label):
    worst = 0.0
    for x, y in zip(xs, ys):
        err = abs(spline(x) - y)
        worst = max(worst, err)
        assert err <= TOL_KNOT, (
            "%s: knot x=%.6g has interpolation error %.3e" % (label, x, err))
    print("  [OK] interpolation: %d knots, max |S(x_i)-y_i| = %.3e"
          % (len(xs), worst))


def assert_continuity(spline, label, verbose=True):
    rows = spline.continuity_report()
    if not rows:
        print("  [OK] continuity: single segment, no interior knots")
        return
    if verbose:
        print("  knot x        S'(left)     S'(right)    |dS'|      "
              "S''(left)    S''(right)   |dS''|")
        for r in rows:
            print("  %-12.6f %-12.6f %-12.6f %-10.2e %-12.6f %-12.6f %.2e"
                  % (r["knot"], r["d1_left"], r["d1_right"], r["d1_diff"],
                     r["d2_left"], r["d2_right"], r["d2_diff"]))
    w1 = max(r["d1_diff"] for r in rows)
    w2 = max(r["d2_diff"] for r in rows)
    assert w1 <= TOL_CONT, "%s: C1 gap %.3e" % (label, w1)
    assert w2 <= TOL_CONT, "%s: C2 gap %.3e" % (label, w2)
    print("  [OK] continuity: max |dS'| = %.3e, max |dS''| = %.3e" % (w1, w2))


def assert_boundary(spline, label):
    kind_l = spline.bc_left[0]
    kind_r = spline.bc_right[0]
    if kind_l == "natural":
        v = spline.second_derivative_on_segment(0, spline.xs[0])
        assert abs(v) <= TOL_BC, "%s: left natural bc violated" % label
    else:
        v = spline.derivative_on_segment(0, spline.xs[0])
        assert abs(v - spline.bc_left[1]) <= TOL_BC, \
            "%s: left clamped bc violated" % label
    if kind_r == "natural":
        v = spline.second_derivative_on_segment(spline.n - 1, spline.xs[-1])
        assert abs(v) <= TOL_BC, "%s: right natural bc violated" % label
    else:
        v = spline.derivative_on_segment(spline.n - 1, spline.xs[-1])
        assert abs(v - spline.bc_right[1]) <= TOL_BC, \
            "%s: right clamped bc violated" % label
    print("  [OK] boundary: left=%s right=%s" % (spline.bc_left, spline.bc_right))


def check_spline(spline, xs, ys, label, verbose_continuity=True):
    print(label)
    assert_interpolation(spline, xs, ys, label)
    assert_continuity(spline, label, verbose=verbose_continuity)
    assert_boundary(spline, label)


# --------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------- #
def scenario_two_points():
    xs = [0.0, 2.0]
    ys = [1.0, 5.0]
    sp = CubicSpline(xs, ys)  # natural on both ends
    check_spline(sp, xs, ys, "== two points, natural (reduces to a line) ==")
    # With M_0 = M_1 = 0 the spline is exactly the straight line y = 2x+1
    for x in (0.25, 0.5, 1.0, 1.5, 1.75):
        assert abs(sp(x) - (2.0 * x + 1.0)) <= TOL_KNOT
    print("  [OK] two-point natural spline is the straight line y=2x+1")

    sp2 = CubicSpline(xs, ys, bc_left=("clamped", 0.0),
                      bc_right=("clamped", 0.0))
    check_spline(sp2, xs, ys, "== two points, clamped slope 0/0 ==")
    assert abs(sp2.derivative(0.0)) <= TOL_BC
    assert abs(sp2.derivative(2.0)) <= TOL_BC


def scenario_equally_spaced():
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    ys = [math.sin(x) for x in xs]
    sp = CubicSpline(xs, ys)
    check_spline(sp, xs, ys, "== equally spaced knots, natural ==")
    print(sp.describe_system("  system A M = d (natural, h=1):"))

    sp_c = CubicSpline(xs, ys,
                       bc_left=("clamped", math.cos(xs[0])),
                       bc_right=("clamped", math.cos(xs[-1])))
    check_spline(sp_c, xs, ys, "== equally spaced knots, clamped cos() ==")
    print(sp_c.describe_system("  system A M = d (clamped, h=1):"))

    # Boundary conditions visibly change the curve away from the knots
    diffs = [abs(sp(x) - sp_c(x)) for x in
             (0.25, 0.5, 0.75, 1.5, 2.5, 3.25, 3.5, 3.75)]
    assert max(diffs) > 1e-3, "natural and clamped curves should differ"
    print("  [OK] natural vs clamped differ, max mid-interval gap = %.4f"
          % max(diffs))


def scenario_dense_points():
    random.seed(42)
    n = 40
    xs = sorted(random.uniform(0.0, 10.0) for _ in range(n))
    # ensure strictly increasing after float rounding
    for i in range(1, n):
        if xs[i] <= xs[i - 1]:
            xs[i] = xs[i - 1] + 1e-6
    ys = [math.sin(x) + 0.1 * math.cos(3.0 * x) for x in xs]
    sp = CubicSpline(xs, ys)
    check_spline(sp, xs, ys, "== dense points (n=%d), natural ==" % n,
                 verbose_continuity=False)
    rows = sp.continuity_report()
    worst1 = max(rows, key=lambda r: r["d1_diff"])
    worst2 = max(rows, key=lambda r: r["d2_diff"])
    print("  worst knot x=%.6f: S' left=%.10f right=%.10f"
          % (worst1["knot"], worst1["d1_left"], worst1["d1_right"]))
    print("  worst knot x=%.6f: S'' left=%.10f right=%.10f"
          % (worst2["knot"], worst2["d2_left"], worst2["d2_right"]))


def scenario_missing_endpoint_conditions():
    xs = [0.0, 0.5, 1.5, 2.0, 3.0]
    ys = [0.0, 1.0, 0.5, 2.0, 1.0]

    # Both endpoints missing -> natural on both sides
    sp_none = CubicSpline(xs, ys)
    assert sp_none.bc_left == ("natural",) and sp_none.bc_right == ("natural",)
    check_spline(sp_none, xs, ys, "== missing both endpoint conditions ==")

    # Only left derivative given, right missing -> natural right
    sp_left = CubicSpline(xs, ys, bc_left=("clamped", 1.0))
    assert sp_left.bc_right == ("natural",)
    check_spline(sp_left, xs, ys, "== only left derivative given ==")

    # Only right derivative given
    sp_right = CubicSpline(xs, ys, bc_right=("clamped", -1.0))
    assert sp_right.bc_left == ("natural",)
    check_spline(sp_right, xs, ys, "== only right derivative given ==")


def scenario_mixed_boundary():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [1.0, 0.0, 2.0, 0.5]
    sp = CubicSpline(xs, ys, bc_left=("clamped", -1.0), bc_right=None)
    check_spline(sp, xs, ys, "== mixed: clamped left, natural right ==")


def scenario_error_handling():
    bad_calls = [
        lambda: CubicSpline([0.0], [0.0]),
        lambda: CubicSpline([0.0, 0.0], [1.0, 2.0]),
        lambda: CubicSpline([0.0, 1.0, 0.5], [0.0, 1.0, 2.0]),
        lambda: CubicSpline([0.0, 1.0], [0.0]),
        lambda: CubicSpline([0.0, 1.0], [0.0, 1.0], bc_left=("bogus",)),
        lambda: CubicSpline([0.0, 1.0], [0.0, 1.0], bc_left=("clamped",)),
    ]
    for fn in bad_calls:
        try:
            fn()
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError was not raised")
    sp = CubicSpline([0.0, 1.0], [0.0, 1.0])
    try:
        sp(2.0)
    except ValueError:
        pass
    else:
        raise AssertionError("evaluating outside the range must fail")
    print("== error handling ==")
    print("  [OK] invalid inputs and out-of-range evaluation raise ValueError")


def main():
    scenario_two_points()
    scenario_equally_spaced()
    scenario_dense_points()
    scenario_missing_endpoint_conditions()
    scenario_mixed_boundary()
    scenario_error_handling()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
