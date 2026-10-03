"""Compression-ratio benchmark: ratio vs. point distribution characteristics.

Run: python3 bench.py
"""

import math
import random
import zlib

import tscompress as tc

N = 20000
RAW_PER_POINT = 16  # 8-byte timestamp + 8-byte value


def report(name, ts, vals, value_type=None):
    data = tc.encode(ts, vals, value_type=value_type)
    raw = len(ts) * RAW_PER_POINT
    ratio = raw / len(data)
    bpp = len(data) * 8 / len(ts)
    # general-purpose compressor baseline for context
    raw_bytes = b"".join(
        t.to_bytes(8, "big", signed=True)
        + (v.to_bytes(8, "big", signed=True) if isinstance(v, int)
           else __import__("struct").pack(">d", v))
        for t, v in zip(ts, vals)
    )
    zratio = raw / len(zlib.compress(raw_bytes, 9))
    print(f"{name:<34} {len(data):>10,} {ratio:>8.2f}x {bpp:>9.2f} {zratio:>10.2f}x")
    return len(data)


def main():
    rng = random.Random(2024)
    base = 1700000000000

    print(f"points per dataset: {N:,}   raw size: {N*RAW_PER_POINT:,} bytes "
          f"(8B ts + 8B value)")
    print(f"{'dataset':<34} {'bytes':>10} {'ratio':>8} {'bits/pt':>9} "
          f"{'zlib ratio':>10}")
    print("-" * 76)

    # 1. constant: regular timestamps, constant value
    report("constant (ts regular, v const)",
           [base + i * 1000 for i in range(N)], [23.5] * N)

    # 2. smooth: slow drift + tiny noise (typical monitoring)
    report("smooth (sine + 1e-3 noise)",
           [base + i * 1000 for i in range(N)],
           [math.sin(i * 0.001) * 100 + rng.uniform(-1e-3, 1e-3)
            for i in range(N)])

    # 3. smooth + irregular timestamps (jittered sampling)
    ts_jit, t = [], base
    for _ in range(N):
        ts_jit.append(t)
        t += rng.choice([900, 1000, 1000, 1100, 2500])
    report("smooth + jittered timestamps",
           ts_jit, [math.sin(i * 0.001) * 100 for i in range(N)])

    # 4. random walk (drifting, no structure in increments)
    walk, v = [], 50.0
    for _ in range(N):
        v += rng.gauss(0, 0.1)
        walk.append(v)
    report("random walk (sigma=0.1)",
           [base + i * 1000 for i in range(N)], walk)

    # 5. spiky: constant with occasional violent jumps
    spiky = []
    for i in range(N):
        spiky.append(1e6 if rng.random() < 0.01 else 23.5)
    report("spiky (1% violent jumps)",
           [base + i * 1000 for i in range(N)], spiky)

    # 6. white noise floats (no temporal structure)
    report("white noise floats (uniform 0..1)",
           [base + i * 1000 for i in range(N)],
           [rng.random() for _ in range(N)])

    # 7. int counter (monotonic, small increments)
    counter, c = [], 0
    for _ in range(N):
        c += rng.randint(0, 3)
        counter.append(c)
    report("int counter (+0..3 per step)",
           [base + i * 1000 for i in range(N)], counter)

    # 8. int random 64-bit (worst case for values)
    report("int random 64-bit (worst case)",
           [base + i * 1000 for i in range(N)],
           [rng.getrandbits(63) - 2**62 for _ in range(N)])

    # 9. float random bit patterns (absolute worst case)
    report("float random bits (absolute worst)",
           [base + i * 1000 for i in range(N)],
           [__import__("struct").unpack(
               ">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
            for _ in range(N)])

    # 10. worst case overall: random bits + irregular huge timestamp gaps
    ts_wild, t = [], base
    for _ in range(N):
        ts_wild.append(t)
        t += rng.getrandbits(40)
    report("random bits + wild ts gaps",
           ts_wild,
           [__import__("struct").unpack(
               ">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
            for _ in range(N)])

    # 11. quantized sensor: 0.1-resolution, mostly unchanged between samples
    qs, qv = [], 230
    for _ in range(N):
        if rng.random() < 0.05:
            qv += rng.choice([-1, 1])
        qs.append(qv / 10.0)
    report("quantized sensor (0.1 res, 5% change)",
           [base + i * 1000 for i in range(N)], qs)

    # 12. adversarial: crafted to defeat every coding path.
    # Values alternate between two XOR masks whose leading/trailing-zero
    # windows strictly do not contain each other (A=(clz=1,tz=0) vs
    # B=(clz=0,tz=2)); payloads are randomized so a general-purpose codec
    # cannot exploit repetition either. Each value costs ~77 of 64 bits.
    # Timestamp dod alternates +-2**63, forcing the escape varint (76 bits).
    import struct as _st
    adv, bitsv = [], rng.getrandbits(64)
    for i in range(N):
        adv.append(_st.unpack(">d", bitsv.to_bytes(8, "big"))[0])
        if i % 2 == 0:
            mask = (1 << 62) | (1 << 0) | (rng.getrandbits(61) << 1)
        else:
            mask = (1 << 63) | (1 << 2) | (rng.getrandbits(60) << 3)
        bitsv ^= mask
    ts_adv, t = [], base
    D = 2**62  # dod reaches +-2**63 -> escape path; t stays in int64
    for i in range(N):
        ts_adv.append(t)
        t += D if i % 2 == 0 else -D
    report("adversarial (crafted worst case)", ts_adv, adv)

    print("-" * 76)
    print("ratio = raw(16B/pt) / compressed;  bits/pt = compressed bits per point")


if __name__ == "__main__":
    main()
