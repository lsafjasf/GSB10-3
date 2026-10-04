"""Radix-2 FFT / inverse FFT and FFT-based linear convolution.

Pure Python 3 standard library (cmath, math). No third-party dependencies.

Zero-padding rules (see README.md for the discussion of their effects):
  * fft(x) pads x with zeros up to the smallest power of two N >= len(x).
    The result is therefore the DFT of the zero-extended sequence, evaluated
    at the usual N frequency bins; it is *not* the DFT of the original-length
    sequence (bin positions differ when len(x) is not a power of two).
  * fft_convolve(a, b) pads both inputs up to the smallest power of two
    N >= len(a) + len(b) - 1, which is exactly long enough to hold the full
    linear convolution without circular aliasing.
"""

import cmath
import math


def is_power_of_two(n):
    return isinstance(n, int) and n >= 1 and (n & (n - 1)) == 0


def next_power_of_two(n):
    """Smallest power of two that is >= n (n must be a positive integer)."""
    if not isinstance(n, int) or n < 1:
        raise ValueError("n must be a positive integer")
    return 1 << (n - 1).bit_length()


def _as_complex(x):
    """Normalize an iterable of numbers into a list of complex."""
    out = []
    for v in x:
        if isinstance(v, complex):
            out.append(v)
        else:
            out.append(complex(float(v), 0.0))
    return out


def fft(x, n=None):
    """Forward DFT via radix-2 iterative Cooley-Tukey.

    x : iterable of real/complex samples.
    n : optional explicit transform length. If n > len(x), x is zero-padded;
        if n < len(x), x is truncated. n must be a power of two. By default
        n = next_power_of_two(len(x)).

    Returns a list of n complex frequency samples, bin k for k = 0..n-1:
        X[k] = sum_{j=0}^{n-1} x[j] * exp(-2*pi*i*j*k/n)
    """
    a = _as_complex(x)
    if n is None:
        n = next_power_of_two(len(a))
    if not is_power_of_two(n):
        raise ValueError("transform length must be a power of two")
    if len(a) > n:
        a = a[:n]
    else:
        a.extend([0j] * (n - len(a)))

    # In-place bit-reversal permutation.
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    # Butterfly stages: length doubles each stage (2, 4, 8, ... n).
    length = 2
    while length <= n:
        angle = -2.0 * math.pi / length
        wlen = cmath.exp(1j * angle)
        half = length >> 1
        for start in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u = a[start + k]
                t = w * a[start + k + half]
                a[start + k] = u + t
                a[start + k + half] = u - t
                w *= wlen
        length <<= 1
    return a


def ifft(x, n=None):
    """Inverse DFT via radix-2 iterative Cooley-Tukey.

    Accepts the same arguments as fft(). Uses the conjugate identity
    ifft(X) = conj(fft(conj(X))) / n.
    """
    a = _as_complex(x)
    if n is None:
        n = next_power_of_two(len(a))
    if not is_power_of_two(n):
        raise ValueError("transform length must be a power of two")
    if len(a) > n:
        a = a[:n]
    else:
        a.extend([0j] * (n - len(a)))

    conj = [v.conjugate() for v in a]
    spec = fft(conj, n)
    return [v.conjugate() / n for v in spec]


def dft_direct(x):
    """O(n^2) direct DFT, used as a reference in tests/benchmarks."""
    a = _as_complex(x)
    n = len(a)
    out = []
    for k in range(n):
        s = 0j
        for j, v in enumerate(a):
            s += v * cmath.exp(-2j * math.pi * j * k / n)
        out.append(s)
    return out


def convolve_direct(a, b):
    """O(n*m) definition-based discrete linear convolution."""
    a = [complex(float(v)) if not isinstance(v, complex) else v for v in a]
    b = [complex(float(v)) if not isinstance(v, complex) else v for v in b]
    if not a or not b:
        return []
    out = [0j] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        if av == 0j:
            continue
        for j, bv in enumerate(b):
            out[i + j] += av * bv
    return out


def fft_convolve(a, b, tol_clean=True):
    """Linear convolution via FFT (full mode).

    Pads to the smallest power of two >= len(a) + len(b) - 1 so that the
    circular convolution equals the linear convolution. Returns a list of
    complex values (imaginary parts are residual round-off, typically ~1e-15).
    """
    if not a or not b:
        return []
    n = next_power_of_two(len(a) + len(b) - 1)
    fa = fft(a, n)
    fb = fft(b, n)
    fc = [u * v for u, v in zip(fa, fb)]
    c = ifft(fc, n)
    c = c[: len(a) + len(b) - 1]
    if tol_clean:
        # Snap residual imaginary parts produced by round-off to exact zero
        # when they are at floating-point noise level relative to the signal.
        scale = max(
            (abs(v) for v in c),
            default=0.0,
        )
        noise = 1e-12 * max(scale, 1.0)
        c = [complex(v.real, 0.0 if abs(v.imag) <= noise else v.imag) for v in c]
    return c
