"""Benchmarks: FFT vs direct DFT, FFT convolution vs direct convolution.

Run:  python3 bench.py
Writes timing data to timing_data.json and prints a summary table.
"""

import json
import math
import random
import time

from fft import convolve_direct, dft_direct, fft, fft_convolve


def timeit(fn, *args, repeat=1):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn(*args)
        dt = time.perf_counter() - t0
        best = min(best, dt)
    return best


def main():
    random.seed(7)
    rows = []

    # FFT sizes (powers of two, plus one non-power-of-2 to show padding cost).
    fft_sizes = [64, 256, 1024, 4096, 16384, 65536]
    print("== FFT timing (radix-2) ==")
    print(f"{'n':>8} {'fft_ms':>10} {'direct_ms':>12} "
          f"{'fft/nlog2n_ns':>14} {'speedup':>9}")
    for n in fft_sizes:
        x = [random.uniform(-1, 1) for _ in range(n)]
        rep = max(1, 2 ** 18 // n)
        t_fft = timeit(fft, x, repeat=rep)
        t_dir = timeit(dft_direct, x) if n <= 4096 else float("nan")
        per = t_fft / (n * math.log2(n)) * 1e9
        speed = t_dir / t_fft if t_dir == t_dir else float("nan")
        rows.append({"n": n, "fft_ms": t_fft * 1e3,
                     "direct_ms": t_dir * 1e3 if t_dir == t_dir else None,
                     "fft_ns_per_nlog2n": per,
                     "speedup": speed if speed == speed else None})
        print(f"{n:>8} {t_fft*1e3:>10.3f} "
              f"{(t_dir*1e3 if t_dir==t_dir else float('nan')):>12.3f} "
              f"{per:>14.2f} "
              f"{(speed if speed==speed else float('nan')):>9.1f}")

    # Non-power-of-2 length: padded up, cost follows padded size.
    n_odd = 100000  # pads to 131072
    x = [random.uniform(-1, 1) for _ in range(n_odd)]
    t_odd = timeit(fft, x, repeat=3)
    print(f"\nnon-power-of-2 n={n_odd} (padded to 131072): "
          f"{t_odd*1e3:.3f} ms")
    rows.append({"n": n_odd, "fft_ms": t_odd * 1e3, "note": "padded to 131072"})

    # Convolution comparison.
    print("\n== Convolution timing: FFT method vs direct definition ==")
    print(f"{'len':>8} {'fftconv_ms':>12} {'direct_ms':>12} {'speedup':>9}")
    for n in [64, 256, 1024, 4096, 16384]:
        a = [random.uniform(-1, 1) for _ in range(n)]
        b = [random.uniform(-1, 1) for _ in range(n)]
        rep = max(1, 2 ** 16 // n)
        t_fast = timeit(fft_convolve, a, b, repeat=rep)
        t_dir = timeit(convolve_direct, a, b) if n <= 4096 else float("nan")
        speed = t_dir / t_fast if t_dir == t_dir else float("nan")
        rows.append({"conv_len": n, "fftconv_ms": t_fast * 1e3,
                     "direct_ms": t_dir * 1e3 if t_dir == t_dir else None,
                     "speedup": speed if speed == speed else None})
        print(f"{n:>8} {t_fast*1e3:>12.3f} "
              f"{(t_dir*1e3 if t_dir==t_dir else float('nan')):>12.3f} "
              f"{(speed if speed==speed else float('nan')):>9.1f}")

    with open("timing_data.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print("\n(saved to timing_data.json)")


if __name__ == "__main__":
    main()
