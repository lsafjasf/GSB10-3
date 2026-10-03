"""On-disk page format.

A store file is a concatenation of self-describing pages.  There is no
superblock: after a crash the index may be gone, but every surviving page can
be recognised and validated on its own, which is what the rebuilder relies on.

Page layout (all integers big-endian, unsigned)::

    offset  size  field
    0       4     magic            b"PST1"
    4       8     page_no          logical page number, monotonically assigned
    12      4     page_size        declared page size in bytes (header+payload),
                                   allowed to differ between pages
    16      4     payload_len      number of payload bytes that follow
    20      4     crc32            CRC-32 of page_no..payload_len and payload
    24      N     payload          length-prefixed records

Payload record layout::

    2       key_len   (uint16)
    K       key
    4       value_len (uint32)
    V       value

Because every page carries its own length and checksum, a torn/corrupt page
can be skipped: the scanner searches forward for the next valid magic header
instead of trusting fixed stride (page sizes are allowed to vary).
"""

import struct
import zlib

MAGIC = b"PST1"
HEADER_SIZE = 24
_MAX_U16 = 0xFFFF
_MAX_U32 = 0xFFFFFFFF
_MAX_U64 = 0xFFFFFFFFFFFFFFFF
MAX_PAYLOAD = _MAX_U32
MAX_KEY_LEN = _MAX_U16
MAX_VALUE_LEN = _MAX_U32


class PageError(ValueError):
    """Raised when a page or record buffer violates the wire format."""


def encode_records(records):
    """Encode an iterable of ``(key, value)`` byte pairs into a payload."""
    parts = []
    for key, value in records:
        if not isinstance(key, (bytes, bytearray)):
            raise PageError("key must be bytes")
        if not isinstance(value, (bytes, bytearray)):
            raise PageError("value must be bytes")
        if len(key) > MAX_KEY_LEN:
            raise PageError("key too long: %d" % len(key))
        if len(value) > MAX_VALUE_LEN:
            raise PageError("value too long: %d" % len(value))
        parts.append(struct.pack(">H", len(key)))
        parts.append(bytes(key))
        parts.append(struct.pack(">I", len(value)))
        parts.append(bytes(value))
    payload = b"".join(parts)
    if len(payload) > MAX_PAYLOAD:
        raise PageError("payload too long: %d" % len(payload))
    return payload


def decode_records(payload):
    """Decode a payload into ``[(key, value), ...]``.

    Raises :class:`PageError` on any truncation or length mismatch so the
    caller can treat the whole page as corrupt.
    """
    records = []
    pos = 0
    size = len(payload)
    while pos < size:
        if pos + 2 > size:
            raise PageError("truncated key length at %d" % pos)
        (key_len,) = struct.unpack_from(">H", payload, pos)
        pos += 2
        if pos + key_len > size:
            raise PageError("truncated key at %d" % pos)
        key = bytes(payload[pos : pos + key_len])
        pos += key_len
        if pos + 4 > size:
            raise PageError("truncated value length at %d" % pos)
        (value_len,) = struct.unpack_from(">I", payload, pos)
        pos += 4
        if pos + value_len > size:
            raise PageError("truncated value at %d" % pos)
        value = bytes(payload[pos : pos + value_len])
        pos += value_len
        records.append((key, value))
    return records


def encode_page(page_no, payload, page_size=None):
    """Encode one page.

    ``page_size`` is purely declarative metadata (the file layout is
    length-prefixed so mixed page sizes need no alignment).  It defaults to the
    on-disk size and lets callers record the nominal page size for diagnostics.
    """
    if not isinstance(page_no, int) or not (0 <= page_no <= _MAX_U64):
        raise PageError("bad page number")
    if not isinstance(payload, (bytes, bytearray)):
        raise PageError("payload must be bytes")
    if len(payload) > MAX_PAYLOAD:
        raise PageError("payload too long: %d" % len(payload))
    payload = bytes(payload)
    wire_size = HEADER_SIZE + len(payload)
    if page_size is None:
        page_size = wire_size
    if not isinstance(page_size, int) or not (1 <= page_size <= _MAX_U32):
        raise PageError("bad page size")
    head_prefix = struct.pack(">QII", page_no, page_size, len(payload))
    crc = zlib.crc32(head_prefix + payload) & _MAX_U32
    return MAGIC + head_prefix + struct.pack(">I", crc) + payload


def header_candidate(data, pos):
    """Validate the header of a page assumed to start at ``pos``.

    Returns ``(page_no, page_size, payload_len, end)`` when the header is
    structurally valid *and* its CRC matches, otherwise ``None``.  This is the
    predicate the resync scanner uses while searching for the next page.
    """
    if data[pos : pos + 4] != MAGIC:
        return None
    if pos + HEADER_SIZE > len(data):
        return None
    page_no, page_size, payload_len = struct.unpack_from(">QII", data, pos + 4)
    end = pos + HEADER_SIZE + payload_len
    if end > len(data):
        return None
    (stored_crc,) = struct.unpack_from(">I", data, pos + 20)
    actual_crc = zlib.crc32(
        bytes(data[pos + 4 : pos + 20]) + bytes(data[pos + HEADER_SIZE : end])
    ) & _MAX_U32
    if stored_crc != actual_crc:
        return None
    return page_no, page_size, payload_len, end


def decode_page(data, pos=0):
    """Decode one page at ``pos`` for writers/tests (strict, no resync)."""
    candidate = header_candidate(data, pos)
    if candidate is None:
        raise PageError("invalid page at offset %d" % pos)
    page_no, page_size, payload_len, end = candidate
    payload = bytes(data[pos + HEADER_SIZE : end])
    return page_no, page_size, payload, end
