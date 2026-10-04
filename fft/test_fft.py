"""Self-tests and benchmarks for fft_lib.  Run: python3 test_fft.py"""

import cmath
import math
import random
import time

from fft_lib import (fft, ifft, fft_auto, convolve, convolve_direct,
                     next_pow2)

FAILURES = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print("[%s] %s %s" % (status, name, detail))
    if not ok:
        FAILURES.append(name)


def max_abs_err(xs, ys):
    return max(abs(x - y) for x, y in zip(xs, ys)) if xs else 0.0


def dft_direct(x, inverse=False):
    """O(n^2) DFT by definition, used as ground truth."""
    n = len(x)
    sign = 1.0 if inverse else -1.0
    out = []
    for k in range(n):
        s = 0.0j
        for t in range(n):
            s += x[t] * cmath.exp(sign * 2j * math.pi * k * t / n)
        out.append(s / n if inverse else s)
    return out


# ---------------------------------------------------------------- correctness
def test_roundtrip():
    for n in (1, 2, 4, 8, 64, 256, 1024):
        random.seed(n)
        x = [complex(random.uniform(-10, 10), random.uniform(-10, 10))
             for _ in range(n)]
        rec = ifft(fft(x))
        err = max_abs_err(x, rec)
        check("roundtrip n=%d" % n, err < 1e-9, "max_err=%.3e" % err)


def test_vs_direct_dft():
    for n in (2, 8, 32, 128):
        random.seed(1000 + n)
        x = [complex(random.uniform(-5, 5), random.uniform(-5, 5))
             for _ in range(n)]
        err = max_abs_err(fft(x), dft_direct(x))
        check("fft vs direct dft n=%d" % n, err < 1e-9, "max_err=%.3e" % err)


def test_convolution():
    cases = [
        (1, 1), (1, 7), (5, 3), (16, 16), (33, 41), (100, 250), (512, 300),
    ]
    for na, nb in cases:
        random.seed(na * 10000 + nb)
        a = [random.uniform(-3, 3) for _ in range(na)]
        b = [random.uniform(-3, 3) for _ in range(nb)]
        fast, ref = convolve(a, b), convolve_direct(a, b)
        err = max_abs_err(fast, ref)
        tol = 1e-9 * max(1.0, max(abs(v) for v in ref))
        check("convolve %dx%d" % (na, nb), err < tol,
              "max_err=%.3e tol=%.1e" % (err, tol))


# ---------------------------------------------------------------- edge cases
def test_edge_cases():
    # length one
    check("fft length-1", fft([3.5]) == [complex(3.5)], str(fft([3.5])))
    check("convolve length-1",
          convolve([2.0], [3.0]) == [6.0], str(convolve([2.0], [3.0])))

    # non-power-of-two length: fft_auto pads; ifft(fft_auto) must recover
    # the original signal followed by zeros.
    for n in (3, 5, 100, 1000):
        random.seed(n)
        x = [random.uniform(-1, 1) for _ in range(n)]
        rec = fft_auto(fft_auto(x), inverse=True)
        err = max_abs_err(x, rec[:n])
        tail = max(abs(v) for v in rec[n:]) if len(rec) > n else 0.0
        check("auto-pad roundtrip n=%d" % n,
              err < 1e-9 and tail < 1e-9,
              "err=%.3e pad_tail=%.3e (padded to %d)"
              % (err, tail, next_pow2(n)))

    # fft on non-pow2 must raise
    try:
        fft([1.0, 2.0, 3.0])
        check("fft rejects non-pow2", False)
    except ValueError:
        check("fft rejects non-pow2", True)

    # all-zero input
    z = fft([0.0] * 64)
    check("fft all-zeros", all(v == 0 for v in z))
    check("convolve all-zeros",
          convolve([0.0] * 50, [1.0] * 30) == [0.0] * 79)

    # extreme magnitudes: 1e150 and 1e-150
    big = [1e150] * 64
    rec = ifft(fft(big))
    err = max_abs_err(big, rec)
    check("roundtrip huge values (1e150)", err / 1e150 < 1e-12,
          "rel_err=%.3e" % (err / 1e150))
    tiny = [1e-150] * 64
    rec = ifft(fft(tiny))
    err = max_abs_err(tiny, rec)
    check("roundtrip tiny values (1e-150)", err / 1e-150 < 1e-12,
          "rel_err=%.3e" % (err / 1e-150))

    # impulse: FFT of delta is flat spectrum
    d = [1.0] + [0.0] * 127
    spec = fft(d)
    err = max(abs(v - 1.0) for v in spec)
    check("fft of impulse is flat", err < 1e-12, "max_err=%.3e" % err)


# ----------------------------------------------------------------- error data
def error_growth():
    print("\n--- error vs length (roundtrip and convolution) ---")
    print("%8s %14s %14s" % ("n", "roundtrip", "conv vs direct"))
    for n in (16, 64, 256, 1024, 4096, 16384, 65536):
        random.seed(42)
        x = [random.uniform(-1, 1) for _ in range(n)]
        err_rt = max_abs_err(x, ifft(fft(x)))
        b = [random.uniform(-1, 1) for _ in range(n)]
        err_cv = max_abs_err(convolve(x, b), convolve_direct(x, b))
        print("%8d %14.3e %14.3e" % (n, err_rt, err_cv))


# ----------------------------------------------------------------- benchmarks
def bench():
    print("\n--- timing: fft (median of repeats) ---")
    print("%8s %12s %14s %14s" % ("n", "time_ms", "us_per_nlog2n", "ratio_vs_prev"))
    prev = None
    for n in (256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072):
        random.seed(n)
        x = [random.uniform(-1, 1) for _ in range(n)]
        reps = max(3, min(200, 2_000_000 // n))
        ts = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fft(x)
            ts.append(time.perf_counter() - t0)
        t = sorted(ts)[len(ts) // 2]
        norm = t * 1e6 / (n * math.log2(n))
        ratio = "" if prev is None else "%.2f" % (t / prev)
        print("%8d %12.3f %14.3f %14s" % (n, t * 1e3, norm, ratio))
        prev = t

    print("\n--- timing: convolve (fft) vs convolve_direct ---")
    print("%8s %14s %14s %10s" % ("n", "fft_conv_ms", "direct_ms", "speedup"))
    for n in (128, 256, 512, 1024, 2048, 4096):
        random.seed(7)
        a = [random.uniform(-1, 1) for _ in range(n)]
        b = [random.uniform(-1, 1) for _ in range(n)]
        t0 = time.perf_counter(); convolve(a, b); t_fast = time.perf_counter() - t0
        t0 = time.perf_counter(); convolve_direct(a, b); t_dir = time.perf_counter() - t0
        print("%8d %14.3f %14.3f %9.1fx" % (n, t_fast * 1e3, t_dir * 1e3,
                                            t_dir / t_fast))


if __name__ == "__main__":
    test_roundtrip()
    test_vs_direct_dft()
    test_convolution()
    test_edge_cases()
    error_growth()
    bench()
    print("\n%s" % ("ALL TESTS PASSED" if not FAILURES
                    else "FAILURES: %s" % FAILURES))
    raise SystemExit(1 if FAILURES else 0)
