"""Adaptive step-size ODE integrator (pure standard library, Python 3.8+).

Integrates y' = f(t, y) with embedded Runge-Kutta pairs:

  * ``rkf45`` -- Runge-Kutta-Fehlberg 4(5): a 4th-order and a 5th-order
    method sharing the same stages.  Their difference estimates the local
    error and drives step acceptance / rejection.
  * ``bs23``  -- Bogacki-Shampine 2(3): a cheap 2nd/3rd-order pair
    (FSAL), useful as a low-accuracy alternative.

The local-error estimate is compared against a mixed absolute/relative
tolerance.  Steps with err_norm <= 1 are accepted, otherwise rejected and
retried with a smaller step.  A minimum-step criterion (default: 100 *
machine epsilon * |t|) prevents the step size from shrinking forever;
when it triggers, the integrator either stops with
``status='min_step'`` (default) or, with ``on_min_step='force'``, keeps
going at the minimum step and records every such event.
"""

import math
from dataclasses import dataclass, field

EPS = math.ulp(1.0)  # machine epsilon for float64 (~2.22e-16)

METHODS = ("rkf45", "bs23")


# --------------------------------------------------------------------------
# Embedded Runge-Kutta tableaus
# --------------------------------------------------------------------------

class _Method:
    """Embedded RK pair: stages A/c, low-order weights b_lo, high-order b_hi."""

    def __init__(self, name, c, a, b_lo, b_hi, exponent, fsal=False):
        self.name = name
        self.c = c
        self.a = a
        self.b_lo = b_lo
        self.b_hi = b_hi
        self.exponent = exponent  # controller exponent = 1 / (p_lo + 1)
        self.fsal = fsal


_RKF45 = _Method(
    name="rkf45",
    c=(0.0, 1.0 / 4.0, 3.0 / 8.0, 12.0 / 13.0, 1.0, 1.0 / 2.0),
    a=(
        (),
        (1.0 / 4.0,),
        (3.0 / 32.0, 9.0 / 32.0),
        (1932.0 / 2197.0, -7200.0 / 2197.0, 7296.0 / 2197.0),
        (439.0 / 216.0, -8.0, 3680.0 / 513.0, -845.0 / 4104.0),
        (-8.0 / 27.0, 2.0, -3544.0 / 2565.0, 1859.0 / 4104.0, -11.0 / 40.0),
    ),
    b_lo=(25.0 / 216.0, 0.0, 1408.0 / 2565.0, 2197.0 / 4104.0, -1.0 / 5.0, 0.0),
    b_hi=(16.0 / 135.0, 0.0, 6656.0 / 12825.0, 28561.0 / 56430.0,
          -9.0 / 50.0, 2.0 / 55.0),
    exponent=0.2,  # 1/5: local error of the 4th-order solution is O(h^5)
)

_BS23 = _Method(
    name="bs23",
    c=(0.0, 1.0 / 2.0, 3.0 / 4.0, 1.0),
    a=(
        (),
        (1.0 / 2.0,),
        (0.0, 3.0 / 4.0),
        (2.0 / 9.0, 1.0 / 3.0, 4.0 / 9.0),
    ),
    b_lo=(7.0 / 24.0, 1.0 / 4.0, 1.0 / 3.0, 1.0 / 8.0),
    b_hi=(2.0 / 9.0, 1.0 / 3.0, 4.0 / 9.0, 0.0),
    exponent=1.0 / 3.0,  # 1/3: local error of the 2nd-order solution is O(h^3)
    fsal=True,
)

_METHOD_TABLE = {"rkf45": _RKF45, "bs23": _BS23}


# --------------------------------------------------------------------------
# Result object
# --------------------------------------------------------------------------

@dataclass
class IntegrateResult:
    """Outcome of :func:`integrate`.

    status:
      'success'  -- reached t1 within tolerance
      'min_step' -- stopped: required step fell below the minimum step
      'max_steps'-- stopped: exceeded max_steps attempted steps
      'diverged' -- stopped: non-finite state or derivative encountered
    """
    t: float
    y: list
    status: str
    message: str
    n_accepted: int = 0
    n_rejected: int = 0
    nfev: int = 0
    min_step_events: int = 0
    h_last: float = 0.0
    t_events: list = field(default_factory=list)

    @property
    def n_steps(self):
        """Total attempted steps (accepted + rejected)."""
        return self.n_accepted + self.n_rejected

    @property
    def rejection_ratio(self):
        """Fraction of attempted steps that were rejected."""
        total = self.n_steps
        return self.n_rejected / total if total else 0.0


# --------------------------------------------------------------------------
# Core routines
# --------------------------------------------------------------------------

def _embedded_step(f, t, y, f0, h, method):
    """One embedded RK step.  Returns (y_hi, err, f_hi, nfev).

    y_hi  -- high-order solution at t + h
    err   -- component-wise difference of the high- and low-order solutions
    f_hi  -- derivative at the new point (reusable by FSAL methods)
    """
    n = len(y)
    k = [f0]
    for ci, ai in zip(method.c[1:], method.a[1:]):
        ti = t + ci * h
        yi = list(y)
        for kj, aij in zip(k, ai):
            if aij != 0.0:
                d = aij * h
                for m in range(n):
                    yi[m] += d * kj[m]
        k.append(_safe_f(f, ti, yi, len(y)))
    y_hi = list(y)
    err = [0.0] * n
    for i in range(n):
        acc_hi = 0.0
        acc_diff = 0.0
        for kj, b_hi, b_lo in zip(k, method.b_hi, method.b_lo):
            acc_hi += b_hi * kj[i]
            acc_diff += (b_hi - b_lo) * kj[i]
        y_hi[i] += h * acc_hi
        err[i] = h * acc_diff
    f_hi = k[-1] if method.fsal else _safe_f(f, t + h, y_hi, n)
    return y_hi, err, f_hi, len(method.c) + (0 if method.fsal else 1)


def _error_norm(err, y, y_new, rtol, atol):
    """RMS norm of err / (atol + rtol * max(|y|, |y_new|)).  <= 1 means accept."""
    total = 0.0
    n = len(err)
    for i in range(n):
        scale = atol + rtol * max(abs(y[i]), abs(y_new[i]))
        if scale > 0.0:
            r = err[i] / scale
        else:
            r = 0.0 if err[i] == 0.0 else float("inf")
        total += r * r
    return math.sqrt(total / n)


def _safe_f(f, t, y, n):
    """Call f(t, y), turning overflow / zero-divide / non-finite returns
    into inf so the caller can detect a diverging step instead of crashing."""
    try:
        out = list(f(t, y))
    except (OverflowError, ValueError, ZeroDivisionError):
        return [float("inf")] * n
    safe = []
    for v in out:
        v = float(v)
        safe.append(v if math.isfinite(v) else float("inf"))
    return safe


def _initial_step(f, t0, y0, f0, direction, rtol, atol, method):
    """Select an initial step size (Hairer, Norsett & Wanner, Solving ODEs I)."""
    n = len(y0)
    # Floor the scale so that zero-state components with atol=0 cannot
    # cause a division by zero (their contribution is then zero anyway).
    scale = [max(atol + rtol * abs(v), 1e-150) for v in y0]
    d0 = math.sqrt(sum((y0[i] / scale[i]) ** 2 for i in range(n)) / n)
    d1 = math.sqrt(sum((f0[i] / scale[i]) ** 2 for i in range(n)) / n)
    h0 = 1e-6 if (d0 < 1e-5 or d1 < 1e-5) else 0.01 * d0 / d1
    y1 = [y0[i] + h0 * direction * f0[i] for i in range(n)]
    f1 = _safe_f(f, t0 + h0 * direction, y1, n)
    d2 = math.sqrt(sum(((f1[i] - f0[i]) / scale[i]) ** 2 for i in range(n)) / n) / h0
    if max(d1, d2) <= 1e-15:
        h1 = max(1e-6, h0 * 1e-3)
    else:
        h1 = (0.01 / max(d1, d2)) ** method.exponent
    return min(100.0 * h0, h1)


def integrate(f, t0, y0, t1, rtol=1e-6, atol=1e-9, method="rkf45",
              h0=None, h_min=None, h_max=None, max_steps=100000,
              on_min_step="stop"):
    """Integrate y' = f(t, y) from t0 to t1 with adaptive step size.

    f           -- rhs; takes (t, y) with y a sequence, returns a sequence
    t0, t1      -- integration interval (t1 < t0 integrates backwards)
    y0          -- initial state (scalar or sequence of floats)
    rtol, atol  -- relative / absolute tolerance (per component)
    method      -- 'rkf45' (default) or 'bs23'
    h0          -- initial step (default: automatic)
    h_min       -- minimum allowed step; default 100 * eps * max(|t0|, |t1|),
                   the level below which time increments are lost to float64
                   rounding.  Smaller user values are clamped to this floor.
    h_max       -- maximum step (default: the whole interval)
    max_steps   -- cap on attempted steps (safety net)
    on_min_step -- 'stop': halt with status 'min_step' when the required step
                   falls below h_min; 'force': take the step at h_min anyway
                   and count the event in min_step_events.
    """
    if method not in _METHOD_TABLE:
        raise ValueError("unknown method %r (choose from %s)" % (method, METHODS))
    if on_min_step not in ("stop", "force"):
        raise ValueError("on_min_step must be 'stop' or 'force'")
    if rtol < 0.0 or atol < 0.0 or (rtol == 0.0 and atol == 0.0):
        raise ValueError("rtol/atol must be non-negative and not both zero")
    meth = _METHOD_TABLE[method]

    if isinstance(y0, (int, float)):
        y0 = [float(y0)]
    else:
        y0 = [float(v) for v in y0]
    if t1 == t0:
        return IntegrateResult(t=t0, y=y0, status="success",
                               message="empty interval")

    direction = 1.0 if t1 > t0 else -1.0
    interval = abs(t1 - t0)
    # Minimum-step criterion: below ~100 ulps of |t| the clock itself cannot
    # be advanced reliably in float64, so shrinking further is meaningless.
    h_min_floor = 100.0 * EPS * max(abs(t0), abs(t1), 1.0)
    if h_min is None:
        h_min = h_min_floor
    else:
        h_min = max(float(h_min), h_min_floor)
    if h_max is None:
        h_max = interval

    t = float(t0)
    y = y0
    f_evals = 0
    f_cur = _safe_f(f, t, y, len(y))
    f_evals += 1
    if not all(math.isfinite(v) for v in f_cur):
        return IntegrateResult(t=t, y=y, status="diverged",
                               message="non-finite derivative at t0",
                               nfev=f_evals)

    if h0 is None:
        try:
            h = _initial_step(f, t, y, f_cur, direction, rtol, atol, meth)
        except (OverflowError, ZeroDivisionError):
            h = 1e-6 * interval  # extreme problem scale; start conservatively
    else:
        h = abs(float(h0))
    f_evals += 1 if h0 is None else 0
    h = min(h, h_max)
    h = max(h, h_min)

    res = IntegrateResult(t=t, y=list(y), status="success", message="",
                          h_last=h)
    step_rejected_before = False
    forced_step = False

    while direction * (t1 - t) > 0.0:
        if res.n_steps >= max_steps:
            res.status = "max_steps"
            res.message = ("exceeded max_steps=%d attempted steps at t=%.17g"
                           % (max_steps, t))
            break
        if h < h_min:
            # The controller wants a step smaller than the minimum allowed.
            if on_min_step == "force":
                h = h_min
                forced_step = True
                res.min_step_events += 1
                res.t_events.append(t)
            else:
                res.status = "min_step"
                res.message = (
                    "required step %.3e < h_min %.3e at t=%.17g; "
                    "tolerance is unreachable (roundoff floor) or the "
                    "solution has a singularity" % (h, h_min, t))
                break
        h = min(h, h_max, abs(t1 - t))
        if direction * (t + h - t1) > 0.0:
            h = abs(t1 - t)

        y_new, err, f_new, used = _embedded_step(f, t, y, f_cur,
                                                 direction * h, meth)
        f_evals += used

        if not all(math.isfinite(v) for v in y_new):
            if on_min_step == "force" and h <= h_min:
                res.status = "diverged"
                res.message = ("non-finite state at t=%.17g (min step forced "
                               "%d times); solution likely diverges"
                               % (t, res.min_step_events))
                break
            # Non-finite trial state: reject and shrink.
            err_norm = float("inf")
        else:
            err_norm = _error_norm(err, y, y_new, rtol, atol)

        if err_norm <= 1.0 or forced_step:
            # Accept the step.
            t += direction * h
            y = y_new
            f_cur = f_new
            res.n_accepted += 1
            res.h_last = h
            forced_step = False
            if err_norm == 0.0:
                factor = 10.0
            else:
                factor = min(5.0, max(0.2, 0.9 * err_norm ** (-meth.exponent)))
            if step_rejected_before:
                factor = min(1.0, factor)  # do not grow right after a rejection
            h *= factor
            step_rejected_before = False
        else:
            # Reject the step and retry with a smaller one.
            res.n_rejected += 1
            if math.isinf(err_norm):
                factor = 0.2
            else:
                factor = max(0.2, 0.9 * err_norm ** (-meth.exponent))
            h *= factor
            step_rejected_before = True

    else:
        res.message = "reached t1 within tolerance"

    res.t = t
    res.y = list(y)
    res.nfev = f_evals
    return res
