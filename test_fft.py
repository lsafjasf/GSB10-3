"""Self-tests for fft.py: correctness, edge cases, and error data.

Run:  python3 test_fft.py
Writes error statistics to error_data.json and prints a summary.
"""

import json
import math
import random
import unittest

from fft import (
    convolve_direct,
    dft_direct,
    fft,
    fft_convolve,
    ifft,
    next_power_of_two,
)


def max_abs(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def rms_abs(a, b):
    diffs = [abs(x - y) for x, y in zip(a, b)]
    scale = max(diffs, default=0.0)
    if scale == 0.0:
        return 0.0
    # Scaled accumulation avoids overflow for extreme-magnitude inputs.
    return scale * math.sqrt(
        sum((d / scale) ** 2 for d in diffs) / len(diffs))


class TestFFT(unittest.TestCase):
    def test_length_one(self):
        # FFT of a single sample is the sample itself.
        self.assertEqual(fft([7.0]), [7 + 0j])
        self.assertEqual(fft([1 + 2j])[0], 1 + 2j)
        self.assertAlmostEqual(ifft([3.0])[0].real, 3.0, places=12)

    def test_power_of_two_vs_direct_dft(self):
        random.seed(1)
        for n in (1, 2, 4, 8, 16, 64, 256):
            x = [random.uniform(-10, 10) for _ in range(n)]
            got = fft(x)
            ref = dft_direct(x)
            err = max_abs(got, ref)
            self.assertLess(err, 1e-8 * n + 1e-9, f"n={n} err={err}")

    def test_non_power_of_two_padding(self):
        # len 5 -> padded to 8; fft(x) must equal fft of the padded sequence.
        x = [1.0, 2.0, -1.0, 0.5, 3.0]
        padded = x + [0.0, 0.0, 0.0]
        self.assertEqual([abs(a - b) for a, b in zip(fft(x), fft(padded, 8))],
                         [0.0] * 8)
        self.assertEqual(len(fft(x)), 8)
        # next_power_of_two rule
        self.assertEqual(next_power_of_two(5), 8)
        self.assertEqual(next_power_of_two(8), 8)
        self.assertEqual(next_power_of_two(9), 16)

    def test_roundtrip_ifft(self):
        random.seed(2)
        for n in (1, 2, 3, 7, 16, 100):  # includes non-power-of-2 lengths
            x = [complex(random.uniform(-5, 5), random.uniform(-5, 5))
                 for _ in range(n)]
            back = ifft(fft(x))[:n]
            err = max_abs(back, x)
            self.assertLess(err, 1e-9, f"n={n} err={err}")

    def test_all_zero_input(self):
        for n in (1, 8, 33):
            self.assertEqual(fft([0.0] * n), [0j] * next_power_of_two(n))
        self.assertEqual(fft_convolve([0.0] * 4, [0.0] * 7),
                         [0j] * (4 + 7 - 1))

    def test_extreme_values(self):
        # Very large, very small, and mixed-scale values: FFT must not
        # overflow/infinities and round-trip within relative tolerance.
        x_big = [1e300, -1e300, 1e300, 1e300]
        s = fft(x_big)
        self.assertTrue(all(math.isfinite(v.real) and math.isfinite(v.imag)
                            for v in s))
        back = ifft(s)
        rel = max(abs(a - b) / 1e300 for a, b in zip(back, x_big))
        self.assertLess(rel, 1e-12)

        x_tiny = [1e-300, 1e-300, -1e-300, 0.0]
        s2 = fft(x_tiny)
        self.assertTrue(all(abs(v) <= 1e-299 for v in s2))
        back2 = ifft(s2)
        rel2 = max(abs(a - b) for a, b in zip(back2, x_tiny))
        self.assertLess(rel2, 1e-312)

        x_mixed = [1e300, 1.0, 1e-300, -2.0]
        back3 = ifft(fft(x_mixed))
        # With a 1e300 dynamic range the absolute error floor is
        # ~ eps * max|x| (~2e284); small components cannot be recovered
        # to absolute precision, only relative to the largest one.
        floor = 1e-12 * 1e300
        for got, want in zip(back3, x_mixed):
            self.assertLess(abs(got - want), floor)
        # The dominant component itself is recovered to full relative
        # precision.
        self.assertLess(abs(back3[0] - 1e300) / 1e300, 1e-12)

    def test_convolution_vs_definition(self):
        random.seed(3)
        cases = [
            ([1.0], [1.0]),                      # length 1 x 1
            ([1.0, 2.0, 3.0], [4.0, 5.0]),       # non-power-of-2 lengths
            ([0.0, 0.0, 0.0], [1.0, 2.0]),       # all-zero side
            ([1e300, -1e300], [1.0, 1.0]),       # extremes
        ]
        for _ in range(20):
            la = random.randint(1, 128)
            lb = random.randint(1, 128)
            cases.append(([random.uniform(-10, 10) for _ in range(la)],
                          [random.uniform(-10, 10) for _ in range(lb)]))
        for a, b in cases:
            got = fft_convolve(a, b, tol_clean=False)
            ref = convolve_direct(a, b)
            scale = max((abs(v) for v in ref), default=1.0)
            err = max_abs(got, ref)
            self.assertLess(err, 1e-8 * max(scale, 1.0),
                            f"len {len(a)}x{len(b)} err={err}")

    def test_convolution_known_result(self):
        # (1,2,3) * (4,5) = (4,13,22,15)
        got = [v.real for v in fft_convolve([1, 2, 3], [4, 5])]
        for got_v, exp_v in zip(got, [4, 13, 22, 15]):
            self.assertAlmostEqual(got_v, exp_v, places=10)

    def test_empty_input(self):
        self.assertEqual(fft_convolve([], [1.0, 2.0]), [])
        self.assertEqual(fft_convolve([1.0], []), [])

    def test_bad_length(self):
        with self.assertRaises(ValueError):
            fft([1.0, 2.0, 3.0], n=6)  # 6 is not a power of two


def collect_error_data():
    """Absolute/relative error of FFT convolution vs direct definition."""
    random.seed(42)
    rows = []
    for la, lb in [(1, 1), (3, 5), (16, 16), (63, 65),
                   (128, 128), (257, 255), (512, 512), (1023, 1025)]:
        a = [random.uniform(-1, 1) for _ in range(la)]
        b = [random.uniform(-1, 1) for _ in range(lb)]
        ref = convolve_direct(a, b)
        got = fft_convolve(a, b, tol_clean=False)
        peak = max(abs(v) for v in ref)
        errs = [abs(x - y) for x, y in zip(got, ref)]
        rows.append({
            "len_a": la,
            "len_b": lb,
            "out_len": len(ref),
            "max_abs_error": max(errs),
            "rms_abs_error": rms_abs(got, ref),
            "max_relative_error": max(errs) / peak,
        })
    # Extreme-value row separately (absolute scale differs).
    a = [1e300, -1e300, 1e300]
    b = [1.0, -1.0]
    ref = convolve_direct(a, b)
    got = fft_convolve(a, b, tol_clean=False)
    errs = [abs(x - y) for x, y in zip(got, ref)]
    rows.append({
        "len_a": 3, "len_b": 2, "out_len": 4,
        "max_abs_error": max(errs),
        "rms_abs_error": rms_abs(got, ref),
        "max_relative_error": max(errs) / 1e300,
        "note": "extreme scale ~1e300",
    })
    with open("error_data.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    return rows


if __name__ == "__main__":
    data = collect_error_data()
    print("== Convolution error: FFT method vs direct definition ==")
    print(f"{'len_a':>5} {'len_b':>5} {'max_abs':>12} {'rms_abs':>12} "
          f"{'max_rel':>10}")
    for r in data:
        print(f"{r['len_a']:>5} {r['len_b']:>5} "
              f"{r['max_abs_error']:>12.3e} {r['rms_abs_error']:>12.3e} "
              f"{r['max_relative_error']:>10.2e}")
    print("\n(saved to error_data.json)\n")
    unittest.main(verbosity=2, exit=False)
