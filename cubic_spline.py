"""
Cubic spline interpolation using the "method of moments" (三弯矩法).

Only the Python 3 standard library is used.

On interval [x_i, x_{i+1}] with h_i = x_{i+1} - x_i and unknown second
derivatives M_i = S''(x_i), the interpolating cubic is

    S_i(x) = M_i * (x_{i+1}-x)^3 / (6 h_i)
           + M_{i+1} * (x-x_i)^3 / (6 h_i)
           + ( y_i      / h_i - M_i      * h_i / 6) * (x_{i+1}-x)
           + ( y_{i+1}  / h_i - M_{i+1}  * h_i / 6) * (x - x_i)

Boundary conditions supported per endpoint ("left" / "right"):

* "natural"  : S'' = 0 at the endpoint  -> M_0 = 0 (or M_n = 0)
* "clamped"  : S'  = given derivative   -> contributes one equation, e.g.
               2 M_0 + M_1 = 6/h_0 * ((y_1-y_0)/h_0 - y'_0)

If an endpoint condition is omitted it defaults to "natural"
(this is the "端点条件缺失" case).

Unknowns M_0..M_n form a tridiagonal linear system solved by the
Thomas algorithm (追赶法).
"""

from bisect import bisect_right
from math import isfinite


class CubicSpline:
    def __init__(self, xs, ys, bc_left=None, bc_right=None):
        """
        xs, ys : strictly increasing x coordinates and matching y values.
        bc_left/bc_right : None or ("natural") / ("clamped", slope).
            None is treated as ("natural",).
        """
        if len(xs) != len(ys):
            raise ValueError("xs and ys must have equal length")
        if len(xs) < 2:
            raise ValueError("at least two points are required")
        if any(xs[i] >= xs[i + 1] for i in range(len(xs) - 1)):
            raise ValueError("xs must be strictly increasing")

        self.xs = [float(x) for x in xs]
        self.ys = [float(y) for y in ys]
        self.n = len(self.xs) - 1  # number of intervals
        self.bc_left = self._normalize_bc(bc_left, "left")
        self.bc_right = self._normalize_bc(bc_right, "right")

        self.hs = [self.xs[i + 1] - self.xs[i] for i in range(self.n)]

        self.lower, self.diag, self.upper, self.rhs = self._build_system()
        self.solve_steps = self._thomas_steps(
            self.lower, self.diag, self.upper, self.rhs
        )
        self.ms = self.solve_steps["x"]  # M_0 .. M_n

    # ------------------------------------------------------------------ #
    # Boundary condition handling
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_bc(bc, side):
        if bc is None:
            return ("natural",)
        kind = bc[0]
        if kind == "natural":
            return ("natural",)
        if kind == "clamped":
            if len(bc) < 2:
                raise ValueError("%s clamped bc needs a slope" % side)
            slope = float(bc[1])
            if not isfinite(slope):
                raise ValueError("%s clamped slope must be finite" % side)
            return ("clamped", slope)
        raise ValueError("unknown %s boundary condition: %r" % (side, kind))

    # ------------------------------------------------------------------ #
    # Tridiagonal system construction
    # ------------------------------------------------------------------ #
    def _build_system(self):
        """
        Build A M = d for M_0..M_n (size n+1).  Interior row i (1..n-1) is
        the C2/C1 compatibility equation:

            mu_i M_{i-1} + 2 M_i + lambda_i M_{i+1} = d_i

        where lambda_i = h_i/(h_{i-1}+h_i), mu_i = 1 - lambda_i,
        d_i = 6/(h_{i-1}+h_i) *
              ((y_{i+1}-y_i)/h_i - (y_i-y_{i-1})/h_{i-1}).

        Endpoint rows depend on the boundary condition:
        natural : M_endpoint = 0                      -> [0, 1, 0] = 0
        clamped : 2 M_0 + M_1 = 6/h0*(f[x0,x1]-y'_0)
                  M_{n-1} + 2 M_n = 6/h_{n-1}*(y'_n - f[x_{n-1},x_n])
        """
        n = self.n
        m = n + 1  # unknown count
        lower = [0.0] * m  # sub-diagonal   (A[i][i-1])
        diag = [0.0] * m  # main diagonal  (A[i][i])
        upper = [0.0] * m  # super-diagonal (A[i][i+1])
        rhs = [0.0] * m

        # Interior rows
        for i in range(1, n):
            h0, h1 = self.hs[i - 1], self.hs[i]
            lam = h1 / (h0 + h1)
            mu = 1.0 - lam
            slope0 = (self.ys[i] - self.ys[i - 1]) / h0
            slope1 = (self.ys[i + 1] - self.ys[i]) / h1
            lower[i] = mu
            diag[i] = 2.0
            upper[i] = lam
            rhs[i] = 6.0 * (slope1 - slope0) / (h0 + h1)

        # Left endpoint row
        if self.bc_left[0] == "natural":
            diag[0] = 1.0
            upper[0] = 0.0
            rhs[0] = 0.0
        else:  # clamped, S'(x0) = slope
            slope = self.bc_left[1]
            secant = (self.ys[1] - self.ys[0]) / self.hs[0]
            diag[0] = 2.0
            upper[0] = 1.0
            rhs[0] = 6.0 * (secant - slope) / self.hs[0]

        # Right endpoint row
        if self.bc_right[0] == "natural":
            lower[n] = 0.0
            diag[n] = 1.0
            rhs[n] = 0.0
        else:  # clamped, S'(xn) = slope
            slope = self.bc_right[1]
            secant = (self.ys[n] - self.ys[n - 1]) / self.hs[n - 1]
            lower[n] = 1.0
            diag[n] = 2.0
            rhs[n] = 6.0 * (slope - secant) / self.hs[n - 1]

        return lower, diag, upper, rhs

    @staticmethod
    def _thomas_steps(lower, diag, upper, rhs):
        """Solve a tridiagonal system; return intermediate sweep values."""
        m = len(diag)
        cp = [0.0] * m  # transformed upper diagonal
        dp = [0.0] * m  # transformed rhs

        cp[0] = upper[0] / diag[0]
        dp[0] = rhs[0] / diag[0]
        for i in range(1, m):
            denom = diag[i] - lower[i] * cp[i - 1]
            cp[i] = upper[i] / denom if i < m - 1 else 0.0
            dp[i] = (rhs[i] - lower[i] * dp[i - 1]) / denom

        x = [0.0] * m
        x[m - 1] = dp[m - 1]
        for i in range(m - 2, -1, -1):
            x[i] = dp[i] - cp[i] * x[i + 1]
        return {"c_prime": cp, "d_prime": dp, "x": x}

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #
    def _segment(self, x):
        if x < self.xs[0] or x > self.xs[-1]:
            raise ValueError("x=%.12g is outside the interpolation range" % x)
        i = bisect_right(self.xs, x) - 1
        if i == len(self.hs):  # exactly the last knot
            i -= 1
        return i

    def eval_on_segment(self, i, x):
        h = self.hs[i]
        a = self.xs[i + 1] - x
        b = x - self.xs[i]
        mi, mi1 = self.ms[i], self.ms[i + 1]
        return (
            mi * a ** 3 / (6.0 * h)
            + mi1 * b ** 3 / (6.0 * h)
            + (self.ys[i] / h - mi * h / 6.0) * a
            + (self.ys[i + 1] / h - mi1 * h / 6.0) * b
        )

    def derivative_on_segment(self, i, x):
        h = self.hs[i]
        a = self.xs[i + 1] - x
        b = x - self.xs[i]
        mi, mi1 = self.ms[i], self.ms[i + 1]
        return (
            -mi * a ** 2 / (2.0 * h)
            + mi1 * b ** 2 / (2.0 * h)
            - (self.ys[i] / h - mi * h / 6.0)
            + (self.ys[i + 1] / h - mi1 * h / 6.0)
        )

    def second_derivative_on_segment(self, i, x):
        h = self.hs[i]
        a = self.xs[i + 1] - x
        b = x - self.xs[i]
        return self.ms[i] * a / h + self.ms[i + 1] * b / h

    def __call__(self, x):
        return self.eval_on_segment(self._segment(float(x)), float(x))

    def derivative(self, x):
        x = float(x)
        return self.derivative_on_segment(self._segment(x), x)

    def second_derivative(self, x):
        x = float(x)
        return self.second_derivative_on_segment(self._segment(x), x)

    def one_sided_derivatives(self, i, order):
        """
        Derivative of order 1/2 at interior knot x_i taken from the left
        segment (i-1, limit x->x_i+) and right segment (i, x->x_i-).
        """
        x = self.xs[i]
        if order == 1:
            left = self.derivative_on_segment(i - 1, x)
            right = self.derivative_on_segment(i, x)
        elif order == 2:
            left = self.second_derivative_on_segment(i - 1, x)
            right = self.second_derivative_on_segment(i, x)
        else:
            raise ValueError("order must be 1 or 2")
        return left, right

    # ------------------------------------------------------------------ #
    # Reporting helpers
    # ------------------------------------------------------------------ #
    def describe_system(self, name="", indent="  "):
        """Return a human-readable description of A M = d and its solution."""
        lines = []
        if name:
            lines.append(name)
        m = len(self.ms)
        for i in range(m):
            row = [0.0] * m
            if i > 0:
                row[i - 1] = self.lower[i]
            row[i] = self.diag[i]
            if i < m - 1:
                row[i + 1] = self.upper[i]
            row_txt = "[" + " ".join(
                "%7.3f" % v if abs(v) > 1e-12 else "      ." for v in row
            ) + "]"
            lines.append(
                "%s%s [M_%-2d] = %9.4f" % (indent, row_txt, i, self.rhs[i])
            )
        lines.append("%ssolution M = [%s]" % (
            indent, ", ".join("%.6f" % v for v in self.ms)))
        return "\n".join(lines)

    def continuity_report(self):
        """List |S'_left-S'_right| and |S''_left-S''_right| at every knot."""
        rows = []
        for i in range(1, self.n):
            d1l, d1r = self.one_sided_derivatives(i, 1)
            d2l, d2r = self.one_sided_derivatives(i, 2)
            rows.append({
                "knot": self.xs[i],
                "d1_left": d1l, "d1_right": d1r, "d1_diff": abs(d1l - d1r),
                "d2_left": d2l, "d2_right": d2r, "d2_diff": abs(d2l - d2r),
            })
        return rows
