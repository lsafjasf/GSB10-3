# -*- coding: utf-8 -*-
"""仅用标准库实现的 PDF 流过滤器解码。

支持：
- FlateDecode（zlib，含 zlib/deflate 两种头）
- ASCIIHexDecode
- ASCII85Decode（含 Adobe 变体 ~>）
过滤器可以是单个名称或数组，按数组顺序逐层解码。
"""

import base64
import binascii
import re
import zlib

from .objects import Name


class FilterError(Exception):
    pass


def asciihex_decode(data: bytes) -> bytes:
    # 结束符 '>' 之后内容忽略；忽略空白
    end = data.find(b">")
    if end >= 0:
        data = data[:end]
    hexpart = re.sub(rb"\s+", b"", data)
    if len(hexpart) % 2:
        hexpart += b"0"  # PDF 规定奇数个十六进制位时补 0
    try:
        return binascii.unhexlify(hexpart)
    except binascii.Error as exc:
        raise FilterError("ASCIIHex 数据非法: %s" % exc)


def ascii85_decode(data: bytes) -> bytes:
    s = data.strip()
    if s.endswith(b"~>"):
        s = s[:-2]
    s = re.sub(rb"\s+", b"", s)
    # 'z' 是四个零字节的简写
    out = b""
    groups = []
    i = 0
    while i < len(s):
        c = s[i:i + 5]
        if c == b"z":
            groups.append((b"z", 1))
            i += 1
        else:
            groups.append((c, len(c)))
            i += len(c)
    for chunk, n in groups:
        if chunk == b"z":
            out += b"\x00\x00\x00\x00"
            continue
        pad = 5 - n
        chunk += b"u" * pad
        val = 0
        for ch in chunk:
            if ch < 33 or ch > 117:
                raise FilterError("ASCII85 字符越界: %r" % ch)
            val = val * 85 + (ch - 33)
        if val > 0xFFFFFFFF:
            raise FilterError("ASCII85 组溢出")
        b = val.to_bytes(4, "big")
        out += b if pad == 0 else b[:4 - pad]
    return out


def flate_decode(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error:
        try:
            # 极少数写入器省略 zlib 头，直接给 raw deflate
            return zlib.decompress(data, -15)
        except zlib.error as exc:
            raise FilterError("Flate 解压失败: %s" % exc)


_SINGLE = {
    "/Fl": flate_decode,
    "/FlDecode": flate_decode,
    "/FlateDecode": flate_decode,
    "/AHx": asciihex_decode,
    "/ASCIIHexDecode": asciihex_decode,
    "/A85": ascii85_decode,
    "/ASCII85Decode": ascii85_decode,
}


def _decoder_for(name):
    key = str(name)
    fn = _SINGLE.get(key)
    if fn is None and key in ("/Crypt",):
        return None  # Crypt（尤其是 Identity）不改变字节，跳过
    if fn is None:
        raise FilterError("不支持的过滤器: %s" % key)
    return fn


def decode_stream(stream):
    """按 /Filter 链解码 PdfStream，返回解码后的 bytes。"""
    d = stream.dict
    filt = d.get(Name("/Filter"))
    if filt is None:
        return stream.raw
    filters = filt if isinstance(filt, list) else [filt]
    data = stream.raw
    for f in filters:
        fn = _decoder_for(f)
        if fn is not None:
            data = fn(data)
    return data


def supports(filters) -> bool:
    if filters is None:
        return True
    if not isinstance(filters, list):
        filters = [filters]
    for f in filters:
        try:
            _decoder_for(f)
        except FilterError:
            return False
    return True
