"""LZ77 短消息压缩器（支持静态字典）。

仅使用 Python 3 标准库，不调用任何内置压缩模块（zlib/lzma/bz2 等）。
字典作为解压器的"预置窗口"参与 LZ77 匹配，与 zstd dictionary 的思路一致。

二进制格式（小端、字节流）::

    +--------+----------+-----------+----------+----------+
    | MAGIC  | dict_id  | payload   | checksum |
    | 4B     | 1B + N B | 变长      | 8B       |
    +--------+----------+-----------+----------+----------+

    MAGIC    = b"DZC1"
    dict_id  = 1 字节长度 + UTF-8 标识（空串表示未使用字典）
    payload  = token 流（见下）
    checksum = sha256(原始数据) 的前 8 字节，用于完整性校验

token 流：
    - 控制字节最高位为 0：字面量段，低 7 位为 (长度-1)，后跟原始字节（1..128）
    - 控制字节最高位为 1：匹配段，低 7 位为 (匹配长度-4)（4..130），
      后跟 varint(偏移-1)。偏移相对当前输出末尾向前计，可指入字典区。
"""

from __future__ import annotations

import hashlib

MAGIC = b"DZC1"
MIN_MATCH = 4
MAX_MATCH = 130          # 单个 token 能编码的最大匹配长度
MAX_OFFSET = 1 << 15     # 32 KiB 滑动窗口
MAX_CHAIN = 24           # 哈希链最多回溯的候选数
CHECKSUM_LEN = 8


class LZDictError(Exception):
    """所有编解码错误的基类。"""


class DictionaryNotFoundError(LZDictError):
    """压缩数据引用的字典在本地不存在。"""


class CorruptDataError(LZDictError):
    """压缩数据本身损坏或格式非法。"""


class IntegrityError(CorruptDataError):
    """解压结果校验失败（数据损坏或使用了错误的字典）。"""


def _checksum(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()[:CHECKSUM_LEN]


def _encode_varint(n: int, out: bytearray) -> None:
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def _decode_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise CorruptDataError("varint 被截断")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise CorruptDataError("varint 过长")


def _encode_tokens(data: bytes, dictionary: bytes) -> bytes:
    """对 data 做贪心哈希链 LZ77 解析，字典作为前缀窗口。"""
    buf = dictionary + data
    start = len(dictionary)
    n = len(data)
    table: dict[bytes, list[int]] = {}

    # 预先把字典中的位置全部入表，使匹配可以引用字典内容。
    for i in range(0, max(0, start - MIN_MATCH + 1)):
        table.setdefault(bytes(buf[i:i + MIN_MATCH]), []).append(i)

    out = bytearray()
    lit_start = 0

    def flush_literals(upto: int) -> None:
        nonlocal lit_start
        while lit_start < upto:
            run = min(upto - lit_start, 128)
            out.append(run - 1)
            out.extend(data[lit_start:lit_start + run])
            lit_start += run

    i = 0
    while i + MIN_MATCH <= n:
        pos = start + i
        key = bytes(buf[pos:pos + MIN_MATCH])
        best_len = 0
        best_off = 0
        candidates = table.get(key)
        if candidates:
            checked = 0
            for p in reversed(candidates):
                offset = pos - p
                if offset > MAX_OFFSET:
                    break
                length = 0
                end = start + n
                while pos + length < end and buf[p + length] == buf[pos + length]:
                    length += 1
                if length > best_len:
                    best_len = length
                    best_off = offset
                checked += 1
                if checked >= MAX_CHAIN:
                    break
        # 远偏移需要 2 字节 varint，4 字节匹配省不下空间，要求更长才值得编码。
        min_useful = MIN_MATCH + (1 if best_off >= 128 else 0)
        if best_len >= min_useful:
            flush_literals(i)
            remaining = best_len
            while remaining > 0:
                length = min(remaining, MAX_MATCH)
                out.append(0x80 | (length - MIN_MATCH))
                _encode_varint(best_off - 1, out)
                remaining -= length
            end = i + best_len
            while i < end:
                pos = start + i
                if pos + MIN_MATCH <= start + n:
                    table.setdefault(bytes(buf[pos:pos + MIN_MATCH]), []).append(pos)
                i += 1
            lit_start = i
        else:
            table.setdefault(key, []).append(pos)
            i += 1
    flush_literals(n)
    return bytes(out)


def _decode_tokens(payload: bytes, dictionary: bytes) -> bytes:
    out = bytearray(dictionary)
    base = len(dictionary)
    pos = 0
    size = len(payload)
    while pos < size:
        control = payload[pos]
        pos += 1
        if control & 0x80:
            length = (control & 0x7F) + MIN_MATCH
            raw_offset, pos = _decode_varint(payload, pos)
            offset = raw_offset + 1
            if offset > len(out):
                raise CorruptDataError("匹配偏移越界（字典可能不正确）")
            src = len(out) - offset
            for k in range(length):
                out.append(out[src + k])
        else:
            length = (control & 0x7F) + 1
            if pos + length > size:
                raise CorruptDataError("字面量段被截断")
            out += payload[pos:pos + length]
            pos += length
    return bytes(out[base:])


def compress(data: bytes, dictionary: bytes = b"", dict_id: str = "") -> bytes:
    """压缩 data。dictionary 非空时必须同时给出对应的 dict_id。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if dictionary and not dict_id:
        raise ValueError("使用字典压缩时必须提供 dict_id")
    encoded_id = dict_id.encode("ascii")
    if len(encoded_id) > 255:
        raise ValueError("dict_id 过长")
    payload = _encode_tokens(data, dictionary)
    return (
        MAGIC
        + bytes([len(encoded_id)])
        + encoded_id
        + payload
        + _checksum(data)
    )


def decompress(blob: bytes, dict_loader=None) -> bytes:
    """解压 blob。

    dict_loader: 可调用对象 dict_id -> bytes（例如 DictStore.load）。
                 当 blob 引用了字典时必须提供；找不到应抛 DictionaryNotFoundError。
    """
    if len(blob) < len(MAGIC) + 1 + CHECKSUM_LEN:
        raise CorruptDataError("数据太短，不是合法的压缩块")
    if blob[:len(MAGIC)] != MAGIC:
        raise CorruptDataError("MAGIC 不匹配，不是本格式数据")
    pos = len(MAGIC)
    id_len = blob[pos]
    pos += 1
    if pos + id_len + CHECKSUM_LEN > len(blob):
        raise CorruptDataError("dict_id 被截断")
    dict_id = blob[pos:pos + id_len].decode("ascii")
    pos += id_len
    payload = blob[pos:len(blob) - CHECKSUM_LEN]
    expected = blob[len(blob) - CHECKSUM_LEN:]

    dictionary = b""
    if dict_id:
        if dict_loader is None:
            raise DictionaryNotFoundError(
                f"数据引用字典 {dict_id!r}，但未提供 dict_loader")
        dictionary = dict_loader(dict_id)
        if dictionary is None:
            raise DictionaryNotFoundError(f"找不到字典 {dict_id!r}")

    data = _decode_tokens(payload, dictionary)
    if _checksum(data) != expected:
        raise IntegrityError("校验和不匹配：数据损坏或使用了错误的字典")
    return data
