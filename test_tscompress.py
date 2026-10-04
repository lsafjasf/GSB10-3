"""Self-tests for tscompress: round-trip, random access, edge cases.

Run:  python3 test_tscompress.py
"""

import math
import random
import struct
import unittest

import tscompress as tsc


def fbits(x):
    return struct.pack(">d", x)


def raw_size(n, vtype):
    return 16 * n  # 8-byte timestamp + 8-byte value per point


class RoundtripTests(unittest.TestCase):
    def assert_int_roundtrip(self, ts, vals, bs=128):
        data = tsc.compress(ts, vals, block_size=bs)
        t2, v2 = tsc.decompress(data)
        self.assertEqual(ts, t2)
        self.assertEqual(vals, v2)
        return data

    def assert_float_roundtrip(self, ts, vals, bs=128):
        data = tsc.compress(ts, vals, block_size=bs)
        t2, v2 = tsc.decompress(data)
        self.assertEqual(ts, t2)
        self.assertEqual(len(vals), len(v2))
        for a, b in zip(vals, v2):
            if math.isnan(a):
                self.assertTrue(math.isnan(b))
                self.assertEqual(fbits(a), fbits(b))  # NaN payload preserved
            else:
                self.assertEqual(fbits(a), fbits(b))  # bit-exact
        return data

    def test_empty(self):
        data = tsc.compress([], [])
        self.assertEqual(tsc.num_blocks(data), 0)
        self.assertEqual(tsc.total_points(data), 0)
        self.assertEqual(tsc.decompress(data), ([], []))

    def test_single_point_int(self):
        data = tsc.compress([1_700_000_000_000], [42])
        self.assertEqual(tsc.num_blocks(data), 1)
        self.assertEqual(tsc.decompress_block(data, 0),
                         ([1_700_000_000_000], [42]))
        self.assertEqual(tsc.decompress(data),
                         ([1_700_000_000_000], [42]))

    def test_single_point_float(self):
        self.assert_float_roundtrip([123456789], [3.141592653589793])

    def test_single_point_block_size_one(self):
        ts = list(range(10))
        vals = [v * v for v in ts]
        data = tsc.compress(ts, vals, block_size=1)
        self.assertEqual(tsc.num_blocks(data), 10)
        for i in range(10):
            self.assertEqual(tsc.decompress_block(data, i),
                             (ts[i:i + 1], vals[i:i + 1]))

    def test_constant_int_series(self):
        ts = [1_000_000 + i * 100 for i in range(1000)]
        vals = [7] * 1000
        data = self.assert_int_roundtrip(ts, vals)
        self.assertLess(len(data), raw_size(1000, tsc.TYPE_INT) * 0.05)

    def test_constant_float_series(self):
        ts = [i * 1_000_000_000 for i in range(1000)]
        vals = [299792458.0] * 1000
        data = self.assert_float_roundtrip(ts, vals)
        self.assertLess(len(data), raw_size(1000, tsc.TYPE_FLOAT) * 0.05)

    def test_smooth_int_random_walk(self):
        rnd = random.Random(1)
        ts, vals = [], []
        t, v = 1_600_000_000_000, 0
        for _ in range(5000):
            t += rnd.randint(99, 101)
            v += rnd.randint(-2, 2)
            ts.append(t)
            vals.append(v)
        self.assert_int_roundtrip(ts, vals)

    def test_smooth_float_random_walk(self):
        rnd = random.Random(2)
        ts, vals = [], []
        t, v = 0, 20.0
        for _ in range(5000):
            t += 1000
            v += rnd.uniform(-0.01, 0.01)
            ts.append(t)
            vals.append(v)
        self.assert_float_roundtrip(ts, vals)

    def test_abrupt_jumps_int(self):
        ts = list(range(0, 2000, 10))
        vals = [0] * len(ts)
        for i in range(0, len(ts), 50):
            vals[i] = (1 << 62) * (1 if i % 100 == 0 else -1)
        self.assert_int_roundtrip(ts, vals)

    def test_abrupt_jumps_float(self):
        rnd = random.Random(3)
        ts = list(range(0, 2000, 10))
        vals = [float(i) for i in range(len(ts))]
        for i in range(0, len(ts), 37):
            vals[i] = rnd.choice([1e308, -1e308, 1.0, -0.0])
        self.assert_float_roundtrip(ts, vals)

    def test_irregular_timestamps(self):
        rnd = random.Random(4)
        ts = sorted(rnd.sample(range(1_000_000_000), 2000))
        vals = [rnd.randint(-1 << 40, 1 << 40) for _ in ts]
        self.assert_int_roundtrip(ts, vals)

    def test_int64_extremes(self):
        ts = [0, 10, 20, 30]
        vals = [tsc.INT64_MIN, tsc.INT64_MAX, 0, tsc.INT64_MIN]
        self.assert_int_roundtrip(ts, vals)

    def test_float_extremes_bit_exact(self):
        vals = [
            0.0, -0.0,
            float("inf"), float("-inf"),
            float("nan"), -float("nan"),
            1.7976931348623157e308,   # DBL_MAX
            2.2250738585072014e-308,  # smallest normal
            5e-324,                   # smallest denormal
            -5e-324,
            1.0, -1.0, 0.1,
            3.141592653589793,
        ]
        ts = list(range(len(vals)))
        # repeat extremes interleaved with jumps across many blocks
        rnd = random.Random(5)
        big = vals * 50
        rnd.shuffle(big)
        self.assert_float_roundtrip(list(range(len(big))), big, bs=31)

    def test_random_float64_bits_bit_exact(self):
        # Worst case for the XOR coder: uniformly random 64-bit patterns.
        rnd = random.Random(6)
        n = 3000
        vals = [struct.unpack(">d", struct.pack(">Q", rnd.getrandbits(64)))[0]
                for _ in range(n)]
        ts = sorted(rnd.sample(range(1 << 40), n))
        data = self.assert_float_roundtrip(ts, vals, bs=256)
        # worst-case expansion bound: <= 77/64 per value + headers (~14%)
        self.assertLessEqual(len(data), raw_size(n, tsc.TYPE_FLOAT) * 1.20)

    def test_adversarial_worst_case_inflation(self):
        # Timestamps: deltas alternate +-2**62 -> DoD needs the 64-bit
        # class (69 bits) every point. Floats: XORs alternate between an
        # explicit 77-bit write (sets window (0,0)) and a 66-bit reuse.
        n = 4000
        ts, t = [], 0
        step = 1 << 62
        for i in range(n):
            ts.append(t)
            t += step if i % 2 == 0 else -step
        vals, b = [], 0
        for i in range(n):
            vals.append(struct.unpack(">d", struct.pack(">Q", b))[0])
            b ^= 0x8000000000000001 if i % 2 == 0 else 0x0123456789ABCDEF
        data = self.assert_float_roundtrip(ts, vals, bs=256)
        # hard bound: (69 + 77) bits/pt + headers < 1.15x raw
        self.assertLessEqual(len(data), raw_size(n, tsc.TYPE_FLOAT) * 1.15)

    def test_random_int64_worst_case(self):
        rnd = random.Random(7)
        n = 3000
        vals = [rnd.randint(tsc.INT64_MIN, tsc.INT64_MAX) for _ in range(n)]
        ts = sorted(rnd.sample(range(1 << 40), n))
        data = tsc.compress(ts, vals, block_size=256)
        self.assertEqual(tsc.decompress(data), (ts, vals))
        self.assertLessEqual(len(data), raw_size(n, tsc.TYPE_INT) * 1.20)


class RandomAccessTests(unittest.TestCase):
    def _check_every_block(self, ts, vals, bs):
        data = tsc.compress(ts, vals, block_size=bs)
        full_ts, full_vals = tsc.decompress(data)
        nb = tsc.num_blocks(data)
        self.assertEqual(nb, (len(ts) + bs - 1) // bs)
        # decode blocks in forward, reverse, and shuffled order
        order = list(range(nb))
        order.reverse()
        for b in order:
            t_b, v_b = tsc.decompress_block(data, b)
            lo, hi = b * bs, min((b + 1) * bs, len(ts))
            self.assertEqual(t_b, full_ts[lo:hi])
            self.assertEqual(v_b, full_vals[lo:hi])
            self.assertEqual(len(t_b), hi - lo)
        rnd = random.Random(99)
        rnd.shuffle(order)
        for b in order:
            t_b, v_b = tsc.decompress_block(data, b)
            lo = b * bs
            self.assertEqual(t_b[0], ts[lo])

    def test_random_access_int(self):
        rnd = random.Random(10)
        n = 4001  # intentionally non-divisible by block sizes
        ts = []
        t = 1_600_000_000
        for _ in range(n):
            t += rnd.randint(0, 1000)
            ts.append(t)
        vals = [0]
        for _ in range(n - 1):
            vals.append(vals[-1] + rnd.randint(-50, 50))
        for bs in (1, 2, 64, 128, 1000):
            self._check_every_block(ts, vals, bs)

    def test_random_access_float(self):
        rnd = random.Random(11)
        n = 3333
        ts = sorted(rnd.sample(range(10_000_000), n))
        vals = []
        v = 100.0
        for _ in range(n):
            v += rnd.gauss(0, 0.5)
            vals.append(v)
        for bs in (1, 37, 256):
            data = tsc.compress(ts, vals, block_size=bs)
            for b in range(tsc.num_blocks(data)):
                lo = b * bs
                t_b, v_b = tsc.decompress_block(data, b)
                self.assertEqual(t_b[0], ts[lo])
                self.assertEqual(fbits(v_b[0]), fbits(vals[lo]))
                hi = min(lo + bs, n)
                self.assertEqual(len(v_b), hi - lo)
                for a, c in zip(v_b, vals[lo:hi]):
                    self.assertEqual(fbits(a), fbits(c))

    def test_block_index_out_of_range(self):
        data = tsc.compress([1, 2], [3, 4], block_size=1)
        with self.assertRaises(IndexError):
            tsc.decompress_block(data, 2)

    def test_block_is_self_contained(self):
        # A single block extracted as "offset-only" must decode without any
        # preceding block bytes: decode at offset with truncated suffix too.
        ts = list(range(0, 500, 1))
        vals = [i * 3 for i in ts]
        data = tsc.compress(ts, vals, block_size=64)
        import struct
        off = struct.unpack(">Q", data[tsc.HEADER_SIZE + 8 * 3:
                                      tsc.HEADER_SIZE + 8 * 4])[0]
        t_b, v_b = tsc._decode_block_at(data, off)
        self.assertEqual(t_b, ts[192:256])
        self.assertEqual(v_b, vals[192:256])


class ValidationTests(unittest.TestCase):
    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            tsc.compress([1, 2], [1])

    def test_mixed_types(self):
        with self.assertRaises(TypeError):
            tsc.compress([1, 2], [1, 2.0])

    def test_int_overflow(self):
        with self.assertRaises(OverflowError):
            tsc.compress([0], [1 << 63])

    def test_bad_block_size(self):
        with self.assertRaises(ValueError):
            tsc.compress([1], [1], block_size=0)

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            tsc.decompress(b"XXXX" + b"\x00" * 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
