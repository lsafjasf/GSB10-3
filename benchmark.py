"""Compression-ratio benchmark: ratio vs. data distribution.

Run:  python3 benchmark.py
"""

import math
import random
import struct

import tscompress as tsc

N = 20000
BS = 256


def gen_timestamps(kind, n, rnd):
    ts, t = [], 1_700_000_000_000_000
    for _ in range(n):
        if kind == "regular":
            t += 1_000_000_000
        elif kind == "jitter":
            t += 1_000_000_000 + rnd.randint(-5_000_000, 5_000_000)
        else:  # burst: long gaps then clusters
            t += rnd.choice([1, 1, 1, 10**9, 10**12])
        ts.append(t)
    return ts


def gen_ints(kind, n, rnd):
    if kind == "constant":
        return [42] * n
    if kind == "linear":
        return [i * 10 for i in range(n)]
    vals, v = [], 1000
    for _ in range(n):
        if kind == "walk_small":
            v += rnd.randint(-3, 3)
        elif kind == "walk_large":
            v += rnd.randint(-10**6, 10**6)
        elif kind == "spiky":
            v += rnd.choice([0] * 99 + [rnd.randint(-10**9, 10**9)])
        elif kind == "random64":
            v = rnd.randint(tsc.INT64_MIN, tsc.INT64_MAX)
        vals.append(v)
    return vals


def gen_floats(kind, n, rnd):
    if kind == "constant":
        return [273.15] * n
    vals, v = [], 20.0
    for i in range(n):
        if kind == "sensor":      # smooth sensor-like: sine + tiny noise
            v = 20.0 + 5.0 * math.sin(i / 500.0) + rnd.uniform(-0.001, 0.001)
        elif kind == "walk":      # random walk, moderate steps
            v += rnd.gauss(0, 0.5)
        elif kind == "spiky":     # flat with abrupt full-range jumps
            v += rnd.choice([0.0] * 99 + [rnd.uniform(-1e6, 1e6)])
        elif kind == "white":     # white noise, no temporal correlation
            v = rnd.uniform(-100.0, 100.0)
        elif kind == "randbits":  # worst case: random 64-bit patterns
            v = struct.unpack(">d", struct.pack(">Q", rnd.getrandbits(64)))[0]
        vals.append(v)
    return vals


def gen_adversarial(n):
    """True worst case: timestamp deltas alternate +-2**62 (DoD needs the
    64-bit class every point) and float XORs alternate between windows
    (lz=0,tz=32) and (lz=32,tz=0) so window reuse never applies."""
    ts, t = [], 0
    d = 1 << 62
    for i in range(n):
        ts.append(t)
        t += d if i % 2 == 0 else -d
    # xor1: lz=0,tz=0 -> explicit 77-bit write, sets window (0,0);
    # xor2: anything fits (0,0) -> 66-bit reuse. Sustained worst = 71.5 b/pt.
    xor1 = 0x8000000000000001
    xor2 = 0x0123456789ABCDEF
    vals, b = [], 0
    for i in range(n):
        vals.append(struct.unpack(">d", struct.pack(">Q", b))[0])
        b ^= xor1 if i % 2 == 0 else xor2
    return ts, vals


def report(name, ts, vals):
    data = tsc.compress(ts, vals, block_size=BS)
    raw = 16 * len(ts)
    ratio = raw / len(data)
    bpp = 8.0 * len(data) / len(ts)
    print("%-34s %10d %10d %8.2fx %8.2f" % (name, raw, len(data), ratio, bpp))
    return len(data), raw


def main():
    rnd = random.Random(2026)
    print("%-34s %10s %10s %8s %8s" %
          ("distribution", "raw(B)", "comp(B)", "ratio", "bits/pt"))
    print("-" * 76)

    for kind in ("regular", "jitter", "burst"):
        ts = gen_timestamps(kind, N, rnd)
        report("ts=%s / int=constant" % kind, ts, [7] * N)

    print("-" * 76)
    ts = gen_timestamps("regular", N, rnd)
    for kind in ("constant", "linear", "walk_small", "walk_large",
                 "spiky", "random64"):
        report("ts=regular / int=%s" % kind, ts, gen_ints(kind, N, rnd))

    print("-" * 76)
    for kind in ("constant", "sensor", "walk", "spiky", "white", "randbits"):
        report("ts=regular / float=%s" % kind, ts, gen_floats(kind, N, rnd))

    print("-" * 76)
    # worst-case inflation check: pure noise in both dimensions
    ts = gen_timestamps("burst", N, rnd)
    comp, raw = report("WORST ts=burst / float=randbits",
                       ts, gen_floats("randbits", N, rnd))
    comp2, _ = report("WORST ts=burst / int=random64",
                      ts, gen_ints("random64", N, rnd))
    print("-" * 76)
    ts_a, vals_a = gen_adversarial(N)
    comp3, _ = report("ADVERSARIAL ts+float worst", ts_a, vals_a)
    print("max inflation vs raw: %.1f%% (float), %.1f%% (int), %.1f%% (adversarial)"
          % ((comp / raw - 1) * 100, (comp2 / raw - 1) * 100,
             (comp3 / raw - 1) * 100))


if __name__ == "__main__":
    main()
