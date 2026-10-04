"""Benchmark / demo driver for adaptive_integrator.

Produces:
  * tolerance sweeps (step counts, rejection ratios, global errors)
  * a comparison of the two embedded pairs (RKF45 vs BS23)
  * boundary cases: singular ODE, too-strict tolerance, min-step handling

Run:  python3 run_benchmarks.py
Output is printed and also written to results/benchmark_results.txt and
results/tolerance_sweep.csv.
"""

import csv
import math
import os

from adaptive_integrator import EPS, integrate

LINES = []


def emit(text=""):
    print(text)
    LINES.append(text)


# --------------------------------------------------------------------------
# Test problems
# --------------------------------------------------------------------------

def prob_smooth():
    """Smooth: y' = -y, y(0)=1, exact y = e^-t."""
    return (lambda t, y: [-y[0]], 0.0, [1.0], 5.0,
            lambda t: [math.exp(-t)])


def prob_oscillator():
    """Smooth system: y'' = -y, y(0)=(1,0), exact (cos t, -sin t)."""
    return (lambda t, y: [y[1], -y[0]], 0.0, [1.0, 0.0], 20.0,
            lambda t: [math.cos(t), -math.sin(t)])


def prob_logistic():
    """Steep transient: y' = y(1-y), y(0)=1e-4; jumps 1e-4 -> 1 near t=9.2."""
    y0 = 1e-4
    exact = lambda t: [1.0 / (1.0 + (1.0 / y0 - 1.0) * math.exp(-t))]
    return (lambda t, y: [y[0] * (1.0 - y[0])], 0.0, [y0], 20.0, exact)


def prob_boundary_layer():
    """Boundary layer: y' = -50(y - cos t), y(0)=0; thin layer near t=0."""
    def exact(t):
        return [(2500.0 * math.cos(t) + 50.0 * math.sin(t)
                 - 2500.0 * math.exp(-50.0 * t)) / 2501.0]
    return (lambda t, y: [-50.0 * (y[0] - math.cos(t))], 0.0, [0.0], 2.0,
            exact)


def global_error(y_num, y_ref):
    return max(abs(a - b) for a, b in zip(y_num, y_ref))


def fmt_row(cols, widths):
    return " ".join(str(c).rjust(w) for c, w in zip(cols, widths))


# --------------------------------------------------------------------------
# Section A: tolerance sweep
# --------------------------------------------------------------------------

def sweep(name, prob, tols, csv_rows):
    f, t0, y0, t1, exact = prob
    emit()
    emit("### %s" % name)
    emit("    rtol=atol*100 swept; error = max abs error vs exact solution at t1")
    hdr = ("rtol", "status", "accept", "reject", "rej%", "steps x(prev)",
           "glob.err", "nfev")
    widths = (9, 9, 7, 7, 7, 15, 11, 8)
    emit(fmt_row(hdr, widths))
    prev = None
    for tol in tols:
        r = integrate(f, t0, list(y0), t1, rtol=tol, atol=tol * 0.01)
        err = global_error(r.y, exact(r.t)) if r.status == "success" else float("nan")
        ratio = ("-" if prev is None else
                 "%.2f (th 2.51)" % (r.n_accepted / prev))
        prev = r.n_accepted
        emit(fmt_row(("%.0e" % tol, r.status, r.n_accepted, r.n_rejected,
                      "%.2f" % (100.0 * r.rejection_ratio), ratio,
                      "%.3e" % err, r.nfev), widths))
        csv_rows.append([name, "rkf45", tol, tol * 0.01, r.status,
                         r.n_accepted, r.n_rejected,
                         "%.6f" % r.rejection_ratio,
                         "%.6e" % err, r.nfev])


# --------------------------------------------------------------------------
# Section B: method comparison
# --------------------------------------------------------------------------

def method_comparison(csv_rows):
    emit()
    emit("### Method comparison on y' = -y, [0, 5], rtol=1e-6, atol=1e-8")
    hdr = ("method", "order", "accept", "reject", "rej%", "nfev", "glob.err")
    widths = (8, 7, 7, 7, 7, 8, 11)
    emit(fmt_row(hdr, widths))
    for meth, order in (("rkf45", "4/5"), ("bs23", "2/3")):
        r = integrate(lambda t, y: [-y[0]], 0.0, [1.0], 5.0,
                      rtol=1e-6, atol=1e-8, method=meth)
        err = abs(r.y[0] - math.exp(-5.0))
        emit(fmt_row((meth, order, r.n_accepted, r.n_rejected,
                      "%.2f" % (100.0 * r.rejection_ratio), r.nfev,
                      "%.3e" % err), widths))
        csv_rows.append(["smooth y'=-y", meth, 1e-6, 1e-8, r.status,
                         r.n_accepted, r.n_rejected,
                         "%.6f" % r.rejection_ratio, "%.6e" % err, r.nfev])


# --------------------------------------------------------------------------
# Section C: singular ODE
# --------------------------------------------------------------------------

def singular_case():
    emit()
    emit("### Singular ODE: y' = y^2, y(0)=1  (blow-up y=1/(1-t) at t=1)")
    emit("    min-step criterion: h_min = 100*eps*max(|t0|,|t1|,1) = %.3e"
         % (100.0 * EPS * 2.0))
    r = integrate(lambda t, y: [y[0] ** 2], 0.0, [1.0], 2.0,
                  rtol=1e-8, atol=1e-10)
    emit("    on_min_step='stop'  -> status=%s, stopped at t=%.15f"
         % (r.status, r.t))
    emit("        distance to exact blow-up time t*=1: %.3e" % (1.0 - r.t))
    emit("        accepted=%d rejected=%d (rej %.1f%%); message: %s"
         % (r.n_accepted, r.n_rejected, 100.0 * r.rejection_ratio, r.message))
    rf = integrate(lambda t, y: [y[0] ** 2], 0.0, [1.0], 2.0,
                   rtol=1e-8, atol=1e-10, on_min_step="force")
    emit("    on_min_step='force' -> status=%s after %d forced min-step events"
         % (rf.status, rf.min_step_events))
    emit("        message: %s" % rf.message)
    emit("    conclusion: the integrator refuses to shrink h below the")
    emit("    float64 clock resolution and reports the singularity instead")
    emit("    of hanging; 'force' plows on and detects the divergence.")


# --------------------------------------------------------------------------
# Section D: too-strict tolerance
# --------------------------------------------------------------------------

def too_strict_case():
    emit()
    emit("### Too-strict tolerance: y'' = -100y (omega=10), [0, 20]")
    f = lambda t, y: [y[1], -100.0 * y[0]]
    exact = lambda t: [math.cos(10.0 * t), -10.0 * math.sin(10.0 * t)]

    r1 = integrate(f, 0.0, [1.0, 0.0], 20.0, rtol=1e-16, atol=0.0)
    emit("    rtol=1e-16, atol=0, default limits -> status=%s" % r1.status)
    emit("        %s" % r1.message)
    emit("        accepted=%d rejected=%d, reached t=%.4f of 20"
         % (r1.n_accepted, r1.n_rejected, r1.t))

    r2 = integrate(f, 0.0, [1.0, 0.0], 20.0, rtol=1e-17, atol=0.0,
                   h_min=1e-3)
    emit("    rtol=1e-17, atol=0, user h_min=1e-3 -> status=%s at t=%.3g"
         % (r2.status, r2.t))
    emit("        %s" % r2.message)

    r3 = integrate(f, 0.0, [1.0, 0.0], 20.0, rtol=1e-17, atol=0.0,
                   h_min=1e-3, on_min_step="force")
    err = global_error(r3.y, exact(20.0))
    emit("    same + on_min_step='force' -> status=%s, forced events=%d"
         % (r3.status, r3.min_step_events))
    emit("        global error at t=20: %.3e (tolerance was violated on"
         " every forced step)" % err)
    emit("    conclusion: with float64, rtol below ~1e-15..1e-16 is beyond")
    emit("    the reachable accuracy; the integrator stops with a clear")
    emit("    diagnostic (min_step / max_steps) instead of looping forever.")


def main():
    emit("=" * 76)
    emit("ADAPTIVE STEP-SIZE INTEGRATION -- BENCHMARK REPORT")
    emit("methods: RKF45 (4th/5th-order embedded pair) and BS23 (2nd/3rd pair)")
    emit("local error = difference of the two formulas; accept if err_norm<=1")
    emit("=" * 76)

    csv_rows = []
    tols = [1e-4, 1e-6, 1e-8, 1e-10, 1e-12]
    emit()
    emit("## A. Tolerance sweep: step count must follow tol^(-1/5) for RKF45")
    sweep("A1 smooth: y' = -y on [0, 5]", prob_smooth(), tols, csv_rows)
    sweep("A2 steep: logistic y' = y(1-y), y0=1e-4, [0, 20]",
          prob_logistic(), tols, csv_rows)
    sweep("A3 boundary layer: y' = -50(y - cos t), [0, 2]",
          prob_boundary_layer(), tols, csv_rows)

    emit()
    emit("## B. Two embedded pairs (different orders) at the same tolerance")
    method_comparison(csv_rows)

    emit()
    emit("## C. Equation with a singularity (min-step criterion triggers)")
    singular_case()

    emit()
    emit("## D. Too-strict tolerance (protection against infinite shrinkage)")
    too_strict_case()

    emit()
    emit("## E. Rejection-ratio summary (all sweep rows above)")
    emit("    Rejections cluster where the solution changes rapidly (steep")
    emit("    transient / boundary layer) and where tolerance is tight;")
    emit("    smooth stretches run with ~0% rejections.")

    os.makedirs("results", exist_ok=True)
    with open(os.path.join("results", "benchmark_results.txt"), "w") as fh:
        fh.write("\n".join(LINES) + "\n")
    with open(os.path.join("results", "tolerance_sweep.csv"), "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "method", "rtol", "atol", "status",
                    "n_accepted", "n_rejected", "rejection_ratio",
                    "global_error", "nfev"])
        w.writerows(csv_rows)
    print("\n[wrote results/benchmark_results.txt and results/tolerance_sweep.csv]")


if __name__ == "__main__":
    main()
