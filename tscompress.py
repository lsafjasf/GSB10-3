"""tscompress: time-series compression with random-access blocks (stdlib only).

Layout
------
Stream = header + offset table + byte-aligned blocks. Every block stores the
first timestamp and first value *raw*, so any block can be decoded on its own
(no dependency on previous blocks).

Encoding
--------
Timestamps (int64, ns/ms/epoch/...):
    t0 raw (64 bits); d1 = t1 - t0; thereafter delta-of-delta (DoD).
    DoD / deltas are zigzag mapped and written with 6 width classes
    (0 -> 1 bit, then 7/14/21/32/64 bits with unary prefix).

Integer values (int64):
    v0 raw (64 bits); thereafter zigzag delta with the same width classes.

Float values (IEEE 754 binary64):
    v0 raw (64 bits); thereafter Gorilla-style XOR compression
    (0 -> 1 bit, otherwise leading/trailing-zero window reuse or explicit
    5+6 bit window header). NaN payloads, -0.0, denormals, infinities all
    round-trip bit for bit because only the 64-bit representation is touched.

Worst case per subsequent point: 69 bits (timestamp) + 69 bits (int value)
or 77 bits (float value) vs. 128 raw bits -- never more than ~14% larger
than raw plus the fixed per-block header (20 bytes).
"""

import struct

MAGIC = b"TSC1"
HEADER_SIZE = 4 + 4 + 4 + 8 + 1  # magic, block_size, num_blocks, points, vtype
MASK64 = (1 << 64) - 1
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1

TYPE_INT = 0
TYPE_FLOAT = 1


class BitWriter:
    """MSB-first bit stream, final byte right-padded with zero bits."""

    __slots__ = ("_buf", "_acc", "_nbits")

    def __init__(self):
        self._buf = bytearray()
        self._acc = 0
        self._nbits = 0

    def write_bits(self, value, n):
        if n:
            self._acc = (self._acc << n) | (value & ((1 << n) - 1))
            self._nbits += n
        while self._nbits >= 8:
            shift = self._nbits - 8
            self._buf.append((self._acc >> shift) & 0xFF)
            self._nbits -= 8
            self._acc &= (1 << self._nbits) - 1

    def align(self):
        if self._nbits:
            self._buf.append((self._acc << (8 - self._nbits)) & 0xFF)
            self._acc = 0
            self._nbits = 0

    def getvalue(self):
        self.align()
        return bytes(self._buf)


class BitReader:
    __slots__ = ("_data", "_bp", "_bit")

    def __init__(self, data, byte_pos=0):
        self._data = data
        self._bp = byte_pos
        self._bit = 0

    def read_bits(self, n):
        v = 0
        data = self._data
        while n > 0:
            if self._bit == 8:
                self._bp += 1
                self._bit = 0
            take = min(8 - self._bit, n)
            shift = 8 - self._bit - take
            v = (v << take) | ((data[self._bp] >> shift) & ((1 << take) - 1))
            self._bit += take
            n -= take
        return v


# ---------------------------------------------------------------------------
# signed zigzag values with 6 width classes
# (0 | 10 +7 | 110 +14 | 1110 +21 | 11110 +32 | 11111 +64)
# ---------------------------------------------------------------------------

_WIDTHS = (7, 14, 21, 32)


def _wrap64(v):
    """Reduce an arbitrary int to the unique int64 congruent mod 2**64."""
    return ((v + (1 << 63)) & MASK64) - (1 << 63)


def _zigzag(v):
    return ((v << 1) ^ (v >> 63)) & MASK64


def _unzigzag(z):
    return (z >> 1) ^ -(z & 1)


def _write_signed(bw, v):
    z = _zigzag(v)
    if z == 0:
        bw.write_bits(0b0, 1)
        return
    for idx, width in enumerate(_WIDTHS):
        if z < (1 << width):
            bw.write_bits((1 << (idx + 2)) - 2, idx + 2)
            bw.write_bits(z, width)
            return
    bw.write_bits(0b11111, 5)
    bw.write_bits(z, 64)


def _read_signed(br):
    if br.read_bits(1) == 0:
        return 0
    head = 0b10
    for idx, width in enumerate(_WIDTHS):
        if br.read_bits(1) == 0:
            return _unzigzag(br.read_bits(width))
        head = (head << 1) | 1
    return _unzigzag(br.read_bits(64))


# ---------------------------------------------------------------------------
# float64 Gorilla XOR
# ---------------------------------------------------------------------------

def _f2bits(x):
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def _bits2f(b):
    return struct.unpack(">d", struct.pack(">Q", b & MASK64))[0]


def _write_xor(bw, xor, prev_lz, prev_tz):
    if xor == 0:
        bw.write_bits(0b0, 1)
        return prev_lz, prev_tz
    lz = 64 - xor.bit_length()
    tz = (xor & -xor).bit_length() - 1
    if lz >= prev_lz and tz >= prev_tz:
        bw.write_bits(0b10, 2)
        bw.write_bits(xor >> prev_tz, 64 - prev_lz - prev_tz)
        return prev_lz, prev_tz
    bw.write_bits(0b11, 2)
    if lz > 31:  # 5-bit field, clamp like Gorilla; window stays exact
        lz = 31
    bw.write_bits(lz, 5)
    sig = 64 - lz - tz
    bw.write_bits(sig - 1, 6)
    bw.write_bits(xor >> tz, sig)
    return lz, tz


def _read_xor(br, prev_bits, prev_lz, prev_tz):
    if br.read_bits(1) == 0:
        return prev_bits, prev_lz, prev_tz
    if br.read_bits(1) == 0:
        xor = br.read_bits(64 - prev_lz - prev_tz) << prev_tz
        return prev_bits ^ xor, prev_lz, prev_tz
    lz = br.read_bits(5)
    sig = br.read_bits(6) + 1
    tz = 64 - lz - sig
    xor = br.read_bits(sig) << tz
    return prev_bits ^ xor, lz, tz


# ---------------------------------------------------------------------------
# block encode / decode
# ---------------------------------------------------------------------------

def _encode_block(ts, vals, vtype):
    bw = BitWriter()
    n = len(ts)
    bw.write_bits(n, 32)
    bw.write_bits(ts[0] & MASK64, 64)
    if vtype == TYPE_FLOAT:
        prev_bits = _f2bits(vals[0])
        bw.write_bits(prev_bits, 64)
    else:
        bw.write_bits(vals[0] & MASK64, 64)
    if n == 1:
        return bw.getvalue()

    prev_d = _wrap64(ts[1] - ts[0])
    _write_signed(bw, prev_d)
    if vtype == TYPE_FLOAT:
        bits = _f2bits(vals[1])
        lz, tz = _write_xor(bw, bits ^ prev_bits, 64, 64)
        prev_bits = bits
    else:
        _write_signed(bw, _wrap64(vals[1] - vals[0]))

    prev_t = ts[1]
    for i in range(2, n):
        d = _wrap64(ts[i] - prev_t)
        _write_signed(bw, _wrap64(d - prev_d))
        prev_d, prev_t = d, ts[i]
        if vtype == TYPE_FLOAT:
            bits = _f2bits(vals[i])
            lz, tz = _write_xor(bw, bits ^ prev_bits, lz, tz)
            prev_bits = bits
        else:
            _write_signed(bw, _wrap64(vals[i] - vals[i - 1]))
    return bw.getvalue()


def _decode_block_at(data, offset):
    br = BitReader(data, offset)
    n = br.read_bits(32)
    ts = [0] * n
    t0 = br.read_bits(64)
    if t0 >= 1 << 63:
        t0 -= 1 << 64
    ts[0] = t0
    is_float = data[HEADER_SIZE - 1] == TYPE_FLOAT  # vtype byte in header
    if is_float:
        vals = [0.0] * n
        prev_bits = br.read_bits(64)
        vals[0] = _bits2f(prev_bits)
    else:
        vals = [0] * n
        v0 = br.read_bits(64)
        vals[0] = v0 - (1 << 64) if v0 >= 1 << 63 else v0
    if n == 1:
        return ts, vals

    d = _read_signed(br)
    ts[1] = _wrap64(ts[0] + d)
    if is_float:
        prev_bits, lz, tz = _read_xor(br, prev_bits, 64, 64)
        vals[1] = _bits2f(prev_bits)
    else:
        vals[1] = _wrap64(vals[0] + _read_signed(br))

    prev_d = d
    for i in range(2, n):
        prev_d = _wrap64(prev_d + _read_signed(br))
        ts[i] = _wrap64(ts[i - 1] + prev_d)
        if is_float:
            prev_bits, lz, tz = _read_xor(br, prev_bits, lz, tz)
            vals[i] = _bits2f(prev_bits)
        else:
            vals[i] = _wrap64(vals[i - 1] + _read_signed(br))
    return ts, vals


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _detect_type(values):
    if all(isinstance(v, bool) or isinstance(v, int) for v in values):
        return TYPE_INT
    if all(isinstance(v, float) for v in values):
        return TYPE_FLOAT
    raise TypeError("values must be all int or all float64")


def _check_int64(v, name):
    if not INT64_MIN <= v <= INT64_MAX:
        raise OverflowError("%s outside signed 64-bit range" % name)


def compress(timestamps, values, block_size=128):
    """Compress equal-length sequences into a self-describing byte string.

    timestamps: sequence of ints (int64 domain).
    values:     sequence of all ints (int64 domain) or all floats.
    block_size: points per block; each block is an independent random-access
                entry point (1..65535).
    """
    if len(timestamps) != len(values):
        raise ValueError("timestamps and values must have equal length")
    if not (1 <= block_size <= 65535):
        raise ValueError("block_size must be in 1..65535")
    n = len(timestamps)
    if n == 0:
        return struct.pack(">4sIIQB", MAGIC, block_size, 0, 0, TYPE_INT)

    vtype = _detect_type(values)
    if vtype == TYPE_INT:
        for i, v in enumerate(values):
            _check_int64(v, "values[%d]" % i)
    for i, t in enumerate(timestamps):
        if not isinstance(t, int) or isinstance(t, bool):
            raise TypeError("timestamps[%d] must be int" % i)
        _check_int64(t, "timestamps[%d]" % i)

    num_blocks = (n + block_size - 1) // block_size
    offsets = [0] * num_blocks
    blocks = bytearray()
    for b in range(num_blocks):
        lo = b * block_size
        hi = min(lo + block_size, n)
        offsets[b] = HEADER_SIZE + 8 * num_blocks + len(blocks)
        blocks += _encode_block(timestamps[lo:hi], values[lo:hi], vtype)

    out = bytearray(struct.pack(">4sIIQB", MAGIC, block_size, num_blocks, n, vtype))
    for off in offsets:
        out += struct.pack(">Q", off)
    out += blocks
    return bytes(out)


def _parse_header(data):
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise ValueError("not a tscompress stream")
    block_size, num_blocks, total, vtype = struct.unpack(">IIQB", data[4:HEADER_SIZE])
    return block_size, num_blocks, total, vtype


def num_blocks(data):
    return _parse_header(data)[1]


def total_points(data):
    return _parse_header(data)[2]


def _block_offset(data, block_index, num_blocks):
    if not 0 <= block_index < num_blocks:
        raise IndexError("block index out of range")
    pos = HEADER_SIZE + 8 * block_index
    return struct.unpack(">Q", data[pos:pos + 8])[0]


def decompress_block(data, block_index):
    """Decode a single block independently -> (timestamps, values).

    Works from any block because each block carries raw t0/v0.
    """
    _, nb, _, _ = _parse_header(data)
    off = _block_offset(data, block_index, nb)
    return _decode_block_at(data, off)


def decompress(data):
    """Decode the whole stream -> (timestamps, values)."""
    _, nb, total, _ = _parse_header(data)
    if total == 0:
        return [], []
    ts, vals = [], []
    for b in range(nb):
        t, v = decompress_block(data, b)
        ts += t
        vals += v
    return ts, vals
