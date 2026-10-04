"""Radix-2 iterative FFT / IFFT and FFT-based convolution.

Pure Python 3 standard library only.

Padding rule
------------
``fft`` requires the input length to be a power of two.  ``fft_auto`` and
``convolve`` transparently zero-pad the input to the next power of two
(``next_pow2``).  Effects of zero-padding:

* The transform of a zero-padded signal equals the original DFT evaluated at
  a denser frequency grid (spectral interpolation); no information is added
  or lost, and the inverse transform recovers the original (padded) signal.
* For convolution, padding to at least ``len(a) + len(b) - 1`` makes circular
  convolution coincide with linear convolution, so the first
  ``len(a) + len(b) - 1`` outputs are exactly the linear convolution.
"""

import cmath
import math


def next_pow2(n):
    """Smallest power of two >= n (next_pow2(0) == 1)."""
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def _check_pow2(n):
    if n < 1 or (n & (n - 1)) != 0:
        raise ValueError("length must be a power of two, got %d" % n)


def fft(x, inverse=False):
    """In-place-style iterative radix-2 Cooley-Tukey FFT.

    Returns a new list of complex values.  Length must be a power of two.
    With ``inverse=True`` computes the IDFT scaled by 1/n, so
    ``fft(fft(x), inverse=True) == x`` (up to rounding).
    """
    n = len(x)
    _check_pow2(n)
    a = [complex(v) for v in x]

    # Bit-reversal permutation.
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    # Iterative butterflies, size 2, 4, ..., n.
    size = 2
    while size <= n:
        half = size >> 1
        # Principal twiddle for this stage.
        angle = (2.0 if inverse else -2.0) * math.pi / size
        w_step = cmath.exp(1j * angle)
        for start in range(0, n, size):
            w = 1.0 + 0.0j
            for k in range(start, start + half):
                u = a[k]
                v = a[k + half] * w
                a[k] = u + v
                a[k + half] = u - v
                w *= w_step
        size <<= 1

    if inverse:
        inv_n = 1.0 / n
        a = [v * inv_n for v in a]
    return a


def ifft(x):
    """Inverse FFT (normalized).  Length must be a power of two."""
    return fft(x, inverse=True)


def fft_auto(x, inverse=False):
    """FFT with automatic zero-padding to the next power of two."""
    n = next_pow2(len(x))
    padded = list(x) + [0.0] * (n - len(x))
    return fft(padded, inverse=inverse)


def convolve(a, b):
    """Linear convolution via FFT.

    Pads both inputs to N = next_pow2(len(a) + len(b) - 1) so that circular
    convolution equals linear convolution, then returns the first
    len(a) + len(b) - 1 real values.
    """
    if not a or not b:
        return []
    out_len = len(a) + len(b) - 1
    n = next_pow2(out_len)
    fa = fft(list(a) + [0.0] * (n - len(a)))
    fb = fft(list(b) + [0.0] * (n - len(b)))
    prod = [x * y for x, y in zip(fa, fb)]
    ic = ifft(prod)
    return [v.real for v in ic[:out_len]]


def convolve_direct(a, b):
    """Reference O(n*m) linear convolution, computed by definition."""
    if not a or not b:
        return []
    out = [0.0] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        for j, bv in enumerate(b):
            out[i + j] += av * bv
    return out
