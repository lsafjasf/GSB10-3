"""Self-tests for tscompress: roundtrip, random access, edge cases."""

import math
import random
import struct
import unittest

import tscompress as tc


def bits(v):
    return struct.pack(">d", v)


def assert_floats_bitexact(testcase, expected, actual):
    testcase.assertEqual(len(expected), len(actual))
    for i, (a, b) in enumerate(zip(expected, actual)):
        testcase.assertEqual(
            bits(a), bits(b), f"point {i}: {a!r} != {b!r} (bit pattern differs)"
        )


class RoundTripTests(unittest.TestCase):
    def roundtrip_float(self, ts, vals, **kw):
        data = tc.encode(ts, vals, **kw)
        ts2, vals2 = tc.decode(data)
        self.assertEqual(list(ts), ts2)
        assert_floats_bitexact(self, list(vals), vals2)
        return data

    def roundtrip_int(self, ts, vals, **kw):
        data = tc.encode(ts, vals, **kw)
        ts2, vals2 = tc.decode(data)
        self.assertEqual(list(ts), ts2)
        self.assertEqual(list(vals), vals2)
        return data

    def test_single_point_float(self):
        self.roundtrip_float([1700000000000], [3.14])

    def test_single_point_int(self):
        self.roundtrip_int([1700000000000], [42])

    def test_empty(self):
        data = tc.encode([], [])
        ts, vals = tc.decode(data)
        self.assertEqual(ts, [])
        self.assertEqual(vals, [])

    def test_two_points(self):
        self.roundtrip_float([1000, 2000], [1.5, -2.5])

    def test_constant_series(self):
        ts = [1700000000000 + i * 1000 for i in range(5000)]
        vals = [23.5] * 5000
        data = self.roundtrip_float(ts, vals)
        # constant series should compress extremely well (< 1 byte/point payload)
        self.assertLess(len(data), len(ts) * 2)

    def test_constant_int_series(self):
        ts = [i * 60 for i in range(3000)]
        data = self.roundtrip_int(ts, [7] * 3000)
        self.assertLess(len(data), len(ts) * 2)

    def test_sharp_jumps_float(self):
        # step function: long plateaus with violent jumps
        ts, vals = [], []
        t = 1700000000000
        for i in range(2000):
            ts.append(t)
            t += 1000
            vals.append(0.0 if (i // 100) % 2 == 0 else 1e6)
        self.roundtrip_float(ts, vals)

    def test_sharp_jumps_int(self):
        ts = [i * 1000 for i in range(1000)]
        vals = [0 if i % 2 == 0 else 2**40 for i in range(1000)]
        self.roundtrip_int(ts, vals)

    def test_float_extremes(self):
        nan_payload = struct.unpack(">d", bytes.fromhex("7ff4000000000001"))[0]
        neg_nan = struct.unpack(">d", bytes.fromhex("fff8000000000000"))[0]
        vals = [
            0.0, -0.0, 1.0, -1.0,
            float("inf"), float("-inf"),
            float("nan"), nan_payload, neg_nan,
            5e-324,               # smallest subnormal
            2.2250738585072014e-308,  # smallest normal
            1.7976931348623157e308,   # max double
            -1.7976931348623157e308,
            math.pi, -math.e, 1e-300, 1e300,
        ]
        ts = [1700000000000 + i * 1000 for i in range(len(vals))]
        self.roundtrip_float(ts, vals)

    def test_float_random_bit_patterns(self):
        rng = random.Random(42)
        vals = []
        for _ in range(2000):
            vals.append(struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0])
        ts = [i * 1000 for i in range(len(vals))]
        self.roundtrip_float(ts, vals)

    def test_int_extremes(self):
        vals = [0, 1, -1, 2**63 - 1, -(2**63), 2**31, -(2**31), 10**18, -(10**18)]
        ts = [i * 1000 for i in range(len(vals))]
        self.roundtrip_int(ts, vals)

    def test_irregular_timestamps(self):
        rng = random.Random(7)
        ts, t = [], 1700000000000
        for _ in range(3000):
            ts.append(t)
            t += rng.choice([1, 10, 1000, 999983, 10**6])
        vals = [float(i) for i in range(len(ts))]
        self.roundtrip_float(ts, vals)

    def test_negative_and_huge_timestamp_deltas(self):
        ts = [0, 10**15, 5, -10**15, 2**62, -(2**62), 0, 1]
        vals = [float(i) for i in range(len(ts))]
        self.roundtrip_float(ts, vals)

    def test_block_size_one(self):
        ts = [i * 1000 for i in range(50)]
        vals = [math.sin(i) for i in range(50)]
        self.roundtrip_float(ts, vals, block_size=1)

    def test_block_size_not_dividing_count(self):
        ts = [i * 1000 for i in range(1000)]
        vals = [float(i * i) for i in range(1000)]
        self.roundtrip_float(ts, vals, block_size=333)

    def test_smooth_series(self):
        rng = random.Random(0)
        ts = [1700000000000 + i * 1000 for i in range(10000)]
        vals = [math.sin(i * 0.01) * 100 + rng.uniform(-0.001, 0.001) for i in range(10000)]
        data = self.roundtrip_float(ts, vals)
        self.assertLess(len(data), len(ts) * 16 // 2)  # > 2x compression

    def test_mixed_int_float_rejected_or_coerced(self):
        # homogeneous required unless value_type given; ints coerce to float
        data = tc.encode([1, 2, 3], [1, 2.5, 3], value_type="float")
        _, vals = tc.decode(data)
        assert_floats_bitexact(self, [1.0, 2.5, 3.0], vals)

    def test_bad_magic_rejected(self):
        with self.assertRaises(ValueError):
            tc.decode(b"XXXX" + b"\x00" * 32)

    def test_length_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            tc.encode([1, 2], [1.0])


class RandomAccessTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(1234)
        self.n = 5000
        self.block_size = 128
        self.ts = []
        t = 1700000000000
        for _ in range(self.n):
            self.ts.append(t)
            t += rng.choice([500, 1000, 1000, 1000, 2000])
        self.vals = [math.sin(i * 0.005) * 50 + rng.uniform(-0.5, 0.5)
                     for i in range(self.n)]
        self.data = tc.encode(self.ts, self.vals, block_size=self.block_size)
        self.reader = tc.Reader(self.data)

    def test_block_count(self):
        self.assertEqual(
            self.reader.block_count,
            (self.n + self.block_size - 1) // self.block_size,
        )

    def test_every_block_matches_slice(self):
        for b in range(self.reader.block_count):
            ts_blk, vals_blk = self.reader.decode_block(b)
            lo = b * self.block_size
            hi = min(lo + self.block_size, self.n)
            self.assertEqual(ts_blk, self.ts[lo:hi], f"block {b} timestamps")
            assert_floats_bitexact(
                self, self.vals[lo:hi], vals_blk
            )

    def test_decode_from_any_block_start(self):
        # decoding only later blocks (skipping earlier ones) must be exact
        for b in [1, 7, self.reader.block_count // 2, self.reader.block_count - 1]:
            ts_blk, vals_blk = self.reader.decode_block(b)
            lo = b * self.block_size
            hi = min(lo + self.block_size, self.n)
            self.assertEqual(ts_blk, self.ts[lo:hi])
            assert_floats_bitexact(self, self.vals[lo:hi], vals_blk)

    def test_read_point_all(self):
        for i in range(self.n):
            t, v = self.reader.read_point(i)
            self.assertEqual(t, self.ts[i])
            self.assertEqual(bits(v), bits(self.vals[i]), f"point {i}")

    def test_read_point_boundaries(self):
        for i in [0, 1, 127, 128, 129, self.n - 1]:
            t, v = self.reader.read_point(i)
            self.assertEqual(t, self.ts[i])
            self.assertEqual(bits(v), bits(self.vals[i]))
        with self.assertRaises(IndexError):
            self.reader.read_point(self.n)
        with self.assertRaises(IndexError):
            self.reader.read_point(-1)

    def test_decode_range(self):
        rng = random.Random(9)
        for _ in range(200):
            a = rng.randrange(0, self.n)
            b = rng.randrange(a, self.n + 1)
            ts_seg, vals_seg = self.reader.decode_range(a, b)
            self.assertEqual(ts_seg, self.ts[a:b])
            assert_floats_bitexact(self, self.vals[a:b], vals_seg)

    def test_full_decode_equals_blockwise(self):
        ts_all, vals_all = self.reader.decode()
        ts_cat, vals_cat = [], []
        for b in range(self.reader.block_count):
            t, v = self.reader.decode_block(b)
            ts_cat.extend(t)
            vals_cat.extend(v)
        self.assertEqual(ts_all, ts_cat)
        assert_floats_bitexact(self, vals_cat, vals_all)

    def test_random_access_int_series(self):
        rng = random.Random(5)
        vals = [rng.randint(-10**6, 10**6) for _ in range(777)]
        ts = [i * 1000 for i in range(777)]
        reader = tc.Reader(tc.encode(ts, vals, block_size=64))
        for i in range(777):
            self.assertEqual(reader.read_point(i), (ts[i], vals[i]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
