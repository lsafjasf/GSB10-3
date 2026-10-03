"""Time-series compression codec (stdlib only).

Design (Gorilla-style, block based):
  * Timestamps (integers, any unit): first timestamp per block is stored raw,
    first delta as zigzag varint, subsequent deltas as delta-of-delta with
    Gorilla-style bit packing.
  * Values, handled separately by type:
      - int:   delta from previous value, zigzag varint in the bit stream.
      - float: XOR of the IEEE-754 bit patterns (struct.pack is bit-exact for
               every double, incl. NaN payloads, -0.0, inf, subnormals),
               compressed with Gorilla leading/trailing-zero windows.
  * Random access: the stream is a sequence of self-contained blocks plus a
    footer index (offset, first timestamp, first value of every block), so
    decoding can start at any block without touching earlier data.

Stream layout:
  header : MAGIC(4) | value_type(1) | block_size uvarint | count uvarint
  blocks : block_0 ... block_{k-1}          (each byte-aligned)
  index  : num_blocks uvarint
           then per block: offset_delta uvarint | t0 svarint | v0 raw
  trailer: index_offset uint64 big-endian (last 8 bytes)

Block layout:
  n uvarint | t0 svarint | v0 raw | [delta1 svarint if n>=2] | bitstream
  bitstream per point i in 1..n-1:
      if i >= 2: delta-of-delta code
      value code (float XOR window / int zigzag varint)
"""

import struct

MAGIC = b"TSC1"
VERSION = 1

INT = 0
FLOAT = 1

DEFAULT_BLOCK_SIZE = 128

# ---------------------------------------------------------------------------
# byte-level varints (LEB128)
# ---------------------------------------------------------------------------


def _write_uvarint(buf, value):
    if value < 0:
        raise ValueError("uvarint expects non-negative value")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            buf.append(byte | 0x80)
        else:
            buf.append(byte)
            return


def _read_uvarint(buf, pos):
    result = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _zigzag_encode(n):
    return (n << 1) if n >= 0 else ((-n << 1) - 1)


def _zigzag_decode(u):
    return (u >> 1) if not (u & 1) else -((u >> 1) + 1)


def _write_svarint(buf, value):
    _write_uvarint(buf, _zigzag_encode(value))


def _read_svarint(buf, pos):
    u, pos = _read_uvarint(buf, pos)
    return _zigzag_decode(u), pos


# ---------------------------------------------------------------------------
# bit stream
# ---------------------------------------------------------------------------


class _BitWriter:
    __slots__ = ("buf", "acc", "nbits")

    def __init__(self):
        self.buf = bytearray()
        self.acc = 0
        self.nbits = 0

    def write_bit(self, bit):
        self.acc = (self.acc << 1) | (bit & 1)
        self.nbits += 1
        if self.nbits == 8:
            self.buf.append(self.acc)
            self.acc = 0
            self.nbits = 0

    def write_bits(self, value, n):
        for i in range(n - 1, -1, -1):
            self.write_bit((value >> i) & 1)

    def write_uvarint(self, value):
        while True:
            chunk = value & 0x7F
            value >>= 7
            self.write_bits(chunk, 7)
            self.write_bit(1 if value else 0)
            if not value:
                return

    def align(self):
        if self.nbits:
            self.acc <<= 8 - self.nbits
            self.buf.append(self.acc)
            self.acc = 0
            self.nbits = 0


class _BitReader:
    __slots__ = ("buf", "pos", "bit")

    def __init__(self, buf, pos):
        self.buf = buf
        self.pos = pos
        self.bit = 0

    def read_bit(self):
        b = (self.buf[self.pos] >> (7 - self.bit)) & 1
        self.bit += 1
        if self.bit == 8:
            self.bit = 0
            self.pos += 1
        return b

    def read_bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.read_bit()
        return v

    def read_uvarint(self):
        result = 0
        shift = 0
        while True:
            chunk = self.read_bits(7)
            result |= chunk << shift
            cont = self.read_bit()
            if not cont:
                return result
            shift += 7


# ---------------------------------------------------------------------------
# delta-of-delta (timestamps)
# ---------------------------------------------------------------------------


def _encode_dod(bw, dod):
    if dod == 0:
        bw.write_bit(0)
    elif -63 <= dod <= 64:
        bw.write_bits(0b10, 2)
        bw.write_bits(dod + 63, 7)
    elif -255 <= dod <= 256:
        bw.write_bits(0b110, 3)
        bw.write_bits(dod + 255, 9)
    elif -2047 <= dod <= 2048:
        bw.write_bits(0b1110, 4)
        bw.write_bits(dod + 2047, 12)
    else:
        bw.write_bits(0b1111, 4)
        bw.write_uvarint(_zigzag_encode(dod))


def _decode_dod(br):
    if br.read_bit() == 0:
        return 0
    if br.read_bit() == 0:
        return br.read_bits(7) - 63
    if br.read_bit() == 0:
        return br.read_bits(9) - 255
    if br.read_bit() == 0:
        return br.read_bits(12) - 2047
    return _zigzag_decode(br.read_uvarint())


# ---------------------------------------------------------------------------
# float value coding (bit-exact via IEEE-754 XOR)
# ---------------------------------------------------------------------------


def _float_bits(value):
    return int.from_bytes(struct.pack(">d", value), "big")


def _bits_float(bits):
    return struct.unpack(">d", bits.to_bytes(8, "big"))[0]


def _encode_float_value(bw, xor, window):
    """window is (clz, tz) of the previous non-zero xor, or (None, None)."""
    if xor == 0:
        bw.write_bit(0)
        return window
    bw.write_bit(1)
    clz = 64 - xor.bit_length()
    tz = (xor & -xor).bit_length() - 1
    pclz, ptz = window
    if pclz is not None and pclz <= clz and ptz <= tz:
        bw.write_bit(0)
        bw.write_bits(xor >> ptz, 64 - pclz - ptz)
        return window
    bw.write_bit(1)
    meaningful = 64 - clz - tz
    bw.write_bits(clz, 6)
    bw.write_bits(meaningful % 64, 6)  # 64 stored as 0
    bw.write_bits(xor >> tz, meaningful)
    return (clz, tz)


def _decode_float_value(br, window):
    if br.read_bit() == 0:
        return 0, window
    if br.read_bit() == 0:
        pclz, ptz = window
        meaningful = 64 - pclz - ptz
        return br.read_bits(meaningful) << ptz, window
    clz = br.read_bits(6)
    meaningful = br.read_bits(6) or 64
    tz = 64 - clz - meaningful
    return br.read_bits(meaningful) << tz, (clz, tz)


# ---------------------------------------------------------------------------
# block encode / decode
# ---------------------------------------------------------------------------


def _encode_block(timestamps, values, value_type):
    out = bytearray()
    n = len(timestamps)
    _write_uvarint(out, n)
    _write_svarint(out, timestamps[0])
    if value_type == FLOAT:
        out += struct.pack(">d", values[0])
    else:
        _write_svarint(out, values[0])
    if n >= 2:
        _write_svarint(out, timestamps[1] - timestamps[0])

    bw = _BitWriter()
    prev_delta = timestamps[1] - timestamps[0] if n >= 2 else 0
    window = (None, None)
    prev_value = values[0]
    prev_float_bits = _float_bits(values[0]) if value_type == FLOAT else 0

    for i in range(1, n):
        if i >= 2:
            delta = timestamps[i] - timestamps[i - 1]
            _encode_dod(bw, delta - prev_delta)
            prev_delta = delta
        if value_type == FLOAT:
            cur_bits = _float_bits(values[i])
            window = _encode_float_value(bw, cur_bits ^ prev_float_bits, window)
            prev_float_bits = cur_bits
        else:
            bw.write_uvarint(_zigzag_encode(values[i] - prev_value))
            prev_value = values[i]
    bw.align()
    out += bw.buf
    return bytes(out)


def _decode_block(buf, pos, value_type):
    n, pos = _read_uvarint(buf, pos)
    t0, pos = _read_svarint(buf, pos)
    if value_type == FLOAT:
        first_value = struct.unpack(">d", buf[pos : pos + 8])[0]
        pos += 8
    else:
        first_value, pos = _read_svarint(buf, pos)

    timestamps = [t0]
    values = [first_value]
    if n == 1:
        return timestamps, values

    delta, pos = _read_svarint(buf, pos)
    timestamps.append(t0 + delta)

    br = _BitReader(buf, pos)
    window = (None, None)
    prev_value = first_value
    prev_float_bits = _float_bits(first_value) if value_type == FLOAT else 0

    for i in range(1, n):
        if i >= 2:
            delta += _decode_dod(br)
            timestamps.append(timestamps[-1] + delta)
        if value_type == FLOAT:
            xor, window = _decode_float_value(br, window)
            prev_float_bits ^= xor
            values.append(_bits_float(prev_float_bits))
        else:
            prev_value += _zigzag_decode(br.read_uvarint())
            values.append(prev_value)
    return timestamps, values


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def _detect_type(values, value_type):
    if value_type is not None:
        if value_type not in (INT, FLOAT, "int", "float"):
            raise ValueError("value_type must be INT/FLOAT/'int'/'float'")
        return FLOAT if value_type in (FLOAT, "float") else INT
    if not values:
        return FLOAT
    if all(isinstance(v, bool) is False and isinstance(v, int) for v in values):
        return INT
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return FLOAT
    raise TypeError("values must be all int or all float (or pass value_type=)")


def encode(timestamps, values, block_size=DEFAULT_BLOCK_SIZE, value_type=None):
    """Encode a time series into bytes.

    timestamps: sequence of ints (any unit, need not be sorted).
    values:     sequence of ints or floats (homogeneous), same length.
    """
    timestamps = list(timestamps)
    values = list(values)
    if len(timestamps) != len(values):
        raise ValueError("timestamps and values must have equal length")
    if block_size < 1:
        raise ValueError("block_size must be >= 1")
    vtype = _detect_type(values, value_type)
    if vtype == FLOAT:
        values = [float(v) for v in values]

    out = bytearray()
    out += MAGIC
    out.append(vtype)
    _write_uvarint(out, block_size)
    _write_uvarint(out, len(timestamps))

    index = []
    for start in range(0, len(timestamps), block_size):
        ts_blk = timestamps[start : start + block_size]
        v_blk = values[start : start + block_size]
        index.append((len(out), ts_blk[0], v_blk[0]))
        out += _encode_block(ts_blk, v_blk, vtype)

    index_offset = len(out)
    _write_uvarint(out, len(index))
    prev_offset = 0
    for offset, t0, v0 in index:
        _write_uvarint(out, offset - prev_offset)
        prev_offset = offset
        _write_svarint(out, t0)
        if vtype == FLOAT:
            out += struct.pack(">d", v0)
        else:
            _write_svarint(out, v0)
    out += struct.pack(">Q", index_offset)
    return bytes(out)


class Reader:
    """Random-access decoder for an encoded stream."""

    def __init__(self, data):
        self._data = data
        if len(data) < len(MAGIC) + 1 + 8 or data[:4] != MAGIC:
            raise ValueError("not a tscompress stream")
        pos = 4
        self.value_type = data[pos]
        pos += 1
        self.block_size, pos = _read_uvarint(data, pos)
        self.count, pos = _read_uvarint(data, pos)
        self._blocks_start = pos

        (index_offset,) = struct.unpack(">Q", data[-8:])
        num_blocks, pos = _read_uvarint(data, index_offset)
        self._index = []
        offset = 0
        for _ in range(num_blocks):
            delta, pos = _read_uvarint(data, pos)
            offset += delta
            t0, pos = _read_svarint(data, pos)
            if self.value_type == FLOAT:
                v0 = struct.unpack(">d", data[pos : pos + 8])[0]
                pos += 8
            else:
                v0, pos = _read_svarint(data, pos)
            self._index.append((offset, t0, v0))

    @property
    def block_count(self):
        return len(self._index)

    def decode_block(self, block_index):
        """Decode one block independently -> (timestamps, values)."""
        if not 0 <= block_index < len(self._index):
            raise IndexError("block index out of range")
        offset = self._index[block_index][0]
        return _decode_block(self._data, offset, self.value_type)

    def decode(self):
        """Decode the whole series -> (timestamps, values)."""
        timestamps, values = [], []
        for i in range(len(self._index)):
            ts_blk, v_blk = self.decode_block(i)
            timestamps.extend(ts_blk)
            values.extend(v_blk)
        return timestamps, values

    def read_point(self, i):
        """Random access to a single point -> (timestamp, value)."""
        if not 0 <= i < self.count:
            raise IndexError("point index out of range")
        block = i // self.block_size
        ts_blk, v_blk = self.decode_block(block)
        j = i % self.block_size
        return ts_blk[j], v_blk[j]

    def decode_range(self, start, stop):
        """Decode points [start, stop) -> (timestamps, values)."""
        start = max(0, start)
        stop = min(self.count, stop)
        if start >= stop:
            return [], []
        timestamps, values = [], []
        first_block = start // self.block_size
        last_block = (stop - 1) // self.block_size
        for block in range(first_block, last_block + 1):
            ts_blk, v_blk = self.decode_block(block)
            lo = block * self.block_size
            a = max(start - lo, 0)
            b = min(stop - lo, len(ts_blk))
            timestamps.extend(ts_blk[a:b])
            values.extend(v_blk[a:b])
        return timestamps, values


def decode(data):
    """Decode a full stream -> (timestamps, values)."""
    return Reader(data).decode()
