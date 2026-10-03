"""行存 -> 列存转换库（仅标准库）。

特性：
- 按列聚合取值，空值用独立位图编码（不与任何默认值混淆）。
- 每列自适应选择 游程编码(RLE) / 字典编码(DICT) / 平铺(PLAIN)，取序列化后最小者。
- 每列数据段独立 zlib 压缩，支持按列懒解码（列存查询只读需要的列）。
- 编码前后的取值可逐行还原（round-trip 保证）。

二进制格式：
    magic(4B) | header_len(4B, big endian) | header(JSON) | payload...
header 中每列记录：name, encoding, row_count, 空值位图与数据段的 offset/length,
数据段是否 zlib 压缩。offset 相对 payload 起点（解码时加上 header 长度）。
"""

import json
import struct
import zlib

MAGIC = b"COL1"

# 值类型标签
_TAG_INT = 1
_TAG_FLOAT = 2
_TAG_STR = 3
_TAG_BOOL = 4

ENC_PLAIN = "plain"
ENC_RLE = "rle"
ENC_DICT = "dict"


# ---------------------------------------------------------------- 基础编解码

def _write_varint(n):
    if n < 0:
        raise ValueError("varint 只支持非负整数")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(buf, pos):
    shift = 0
    result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _zigzag(n):
    return (n << 1) ^ (n >> 63) if n >= 0 else ((-n << 1) - 1)


def _write_value(buf, v):
    # 注意 bool 是 int 的子类，必须先判 bool
    if isinstance(v, bool):
        buf.append(_TAG_BOOL)
        buf.append(1 if v else 0)
    elif isinstance(v, int):
        buf.append(_TAG_INT)
        buf.extend(_write_varint(_zigzag(v)))
    elif isinstance(v, float):
        buf.append(_TAG_FLOAT)
        buf.extend(struct.pack("<d", v))
    elif isinstance(v, str):
        data = v.encode("utf-8")
        buf.append(_TAG_STR)
        buf.extend(_write_varint(len(data)))
        buf.extend(data)
    else:
        raise TypeError("不支持的值类型: %r" % type(v))


def _read_value(buf, pos):
    tag = buf[pos]
    pos += 1
    if tag == _TAG_BOOL:
        return bool(buf[pos]), pos + 1
    if tag == _TAG_INT:
        z, pos = _read_varint(buf, pos)
        n = (z >> 1) ^ -(z & 1)
        return n, pos
    if tag == _TAG_FLOAT:
        return struct.unpack_from("<d", buf, pos)[0], pos + 8
    if tag == _TAG_STR:
        n, pos = _read_varint(buf, pos)
        return buf[pos:pos + n].decode("utf-8"), pos + n
    raise ValueError("未知的类型标签: %d" % tag)


# ---------------------------------------------------------------- 列内编码

def _encode_plain(values):
    buf = bytearray()
    buf.extend(_write_varint(len(values)))
    for v in values:
        _write_value(buf, v)
    return bytes(buf)


def _decode_plain(buf):
    n, pos = _read_varint(buf, 0)
    out = []
    for _ in range(n):
        v, pos = _read_value(buf, pos)
        out.append(v)
    return out


def _encode_rle(values):
    """游程编码：值 + 游程长度 交替存储。"""
    buf = bytearray()
    runs = []
    for v in values:
        if runs and runs[-1][0] == v and type(runs[-1][0]) is type(v):
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    buf.extend(_write_varint(len(runs)))
    for v, cnt in runs:
        _write_value(buf, v)
        buf.extend(_write_varint(cnt))
    return bytes(buf)


def _decode_rle(buf):
    n_runs, pos = _read_varint(buf, 0)
    out = []
    for _ in range(n_runs):
        v, pos = _read_value(buf, pos)
        cnt, pos = _read_varint(buf, pos)
        out.extend([v] * cnt)
    return out


def _encode_dict(values):
    """字典编码：字典表 + 每行的字典下标。"""
    dict_vals = []
    index = {}
    idxs = []
    for v in values:
        key = (type(v).__name__, v)
        i = index.get(key)
        if i is None:
            i = len(dict_vals)
            dict_vals.append(v)
            index[key] = i
        idxs.append(i)
    buf = bytearray()
    buf.extend(_write_varint(len(dict_vals)))
    for v in dict_vals:
        _write_value(buf, v)
    buf.extend(_write_varint(len(idxs)))
    for i in idxs:
        buf.extend(_write_varint(i))
    return bytes(buf)


def _decode_dict(buf):
    n_dict, pos = _read_varint(buf, 0)
    dict_vals = []
    for _ in range(n_dict):
        v, pos = _read_value(buf, pos)
        dict_vals.append(v)
    n_idx, pos = _read_varint(buf, pos)
    out = []
    for _ in range(n_idx):
        i, pos = _read_varint(buf, pos)
        out.append(dict_vals[i])
    return out


_ENCODERS = {ENC_PLAIN: _encode_plain, ENC_RLE: _encode_rle, ENC_DICT: _encode_dict}
_DECODERS = {ENC_PLAIN: _decode_plain, ENC_RLE: _decode_rle, ENC_DICT: _decode_dict}


def encode_column(values):
    """编码单列（含 None）。返回 (meta, bitmap_bytes, data_bytes)。

    meta: dict(encoding, row_count, compressed)
    空值用位图单独编码，非空值按行序进入数据段。
    """
    n = len(values)
    bitmap = bytearray((n + 7) // 8)
    non_null = []
    for i, v in enumerate(values):
        if v is None:
            bitmap[i >> 3] |= 1 << (i & 7)
        else:
            non_null.append(v)

    # 自适应选择最小编码
    best_enc, best_data = None, None
    for enc, fn in _ENCODERS.items():
        data = fn(non_null)
        if best_data is None or len(data) < len(best_data):
            best_enc, best_data = enc, data

    compressed = False
    if len(best_data) > 64:  # 太小的段压缩不划算
        z = zlib.compress(best_data, 9)
        if len(z) < len(best_data):
            best_data, compressed = z, True

    meta = {"encoding": best_enc, "row_count": n, "compressed": compressed}
    return meta, bytes(bitmap), best_data


def decode_column(meta, bitmap, data):
    """按列元数据解码，返回含 None 的完整列（长度 = row_count）。"""
    if meta["compressed"]:
        data = zlib.decompress(data)
    non_null = _DECODERS[meta["encoding"]](data)
    n = meta["row_count"]
    out = []
    it = iter(non_null)
    for i in range(n):
        if bitmap[i >> 3] >> (i & 7) & 1:
            out.append(None)
        else:
            out.append(next(it))
    return out


# ---------------------------------------------------------------- 表级接口

def encode_table(columns, rows):
    """把行存（list of tuple/list）转成列存字节串。

    columns: 列名列表；rows: 每行是与 columns 等长的序列。
    """
    ncols = len(columns)
    for r in rows:
        if len(r) != ncols:
            raise ValueError("行列数与列名不一致: %r" % (r,))
    cols_values = [[row[i] for row in rows] for i in range(ncols)]

    col_metas = []
    sections = []
    offset = 0
    for name, values in zip(columns, cols_values):
        meta, bitmap, data = encode_column(values)
        meta["name"] = name
        meta["bitmap_offset"] = offset
        meta["bitmap_len"] = len(bitmap)
        meta["data_offset"] = offset + len(bitmap)
        meta["data_len"] = len(data)
        offset += len(bitmap) + len(data)
        sections.append(bitmap)
        sections.append(data)
        col_metas.append(meta)

    header = {"row_count": len(rows), "columns": col_metas}
    header_bytes = json.dumps(header, ensure_ascii=False).encode("utf-8")
    payload = b"".join(sections)
    return MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes + payload


def _read_header(blob):
    """返回 (header, payload_base)。header 中偏移均相对 payload 起点。"""
    if blob[:4] != MAGIC:
        raise ValueError("不是合法的列存数据")
    (hlen,) = struct.unpack_from(">I", blob, 4)
    header = json.loads(blob[8:8 + hlen].decode("utf-8"))
    return header, 8 + hlen


def list_columns(blob):
    h, _ = _read_header(blob)
    return [c["name"] for c in h["columns"]]


def decode_column_from_blob(blob, name):
    """按列懒解码：只读取并解码指定列（列存查询的核心优势）。"""
    h, base = _read_header(blob)
    for m in h["columns"]:
        if m["name"] == name:
            b0 = base + m["bitmap_offset"]
            d0 = base + m["data_offset"]
            bitmap = blob[b0:b0 + m["bitmap_len"]]
            data = blob[d0:d0 + m["data_len"]]
            return decode_column(m, bitmap, data)
    raise KeyError("列不存在: %r" % name)


def decode_table(blob):
    """整表解码为 (columns, rows)，与 encode_table 的输入逐行一致。"""
    h, _ = _read_header(blob)
    names = [c["name"] for c in h["columns"]]
    cols = [decode_column_from_blob(blob, n) for n in names]
    rows = [tuple(r) for r in zip(*cols)] if names else []
    return names, rows


# ---------------------------------------------------------------- 查询（对拍用）

def row_query(columns, rows, select_cols, where=None):
    """行存查询：先过滤再投影。"""
    idx = {c: i for i, c in enumerate(columns)}
    sel = [idx[c] for c in select_cols]
    out = []
    for row in rows:
        if where is None or where({c: row[idx[c]] for c in columns}):
            out.append(tuple(row[i] for i in sel))
    return out


def col_query(blob, select_cols, where=None, where_cols=()):
    """列存查询：只解码谓词列与投影列，结果与 row_query 逐行一致。"""
    needed = list(dict.fromkeys(list(where_cols) + list(select_cols)))
    decoded = {c: decode_column_from_blob(blob, c) for c in needed}
    h, _ = _read_header(blob)
    n = h["row_count"]
    out = []
    for i in range(n):
        if where is not None:
            env = {c: decoded[c][i] for c in where_cols}
            if not where(env):
                continue
        out.append(tuple(decoded[c][i] for c in select_cols))
    return out


# ---------------------------------------------------------------- 压缩率工具

def row_store_bytes(columns, rows):
    """行存基线：JSON Lines 序列化字节数。"""
    total = 0
    for row in rows:
        total += len(json.dumps(dict(zip(columns, row)),
                                ensure_ascii=False).encode("utf-8")) + 1
    return total


def compression_report(columns, rows):
    """返回 dict：行存/列存字节数与压缩率，以及每列编码方式与空值比例。"""
    blob = encode_table(columns, rows)
    raw = row_store_bytes(columns, rows)
    per_col = []
    n = len(rows)
    for i, c in enumerate(columns):
        nulls = sum(1 for r in rows if r[i] is None)
        meta, _, _ = encode_column([r[i] for r in rows])
        per_col.append({
            "column": c,
            "encoding": meta["encoding"],
            "null_ratio": (nulls / n) if n else 0.0,
        })
    return {
        "row_store_bytes": raw,
        "columnar_bytes": len(blob),
        "compression_ratio": (raw / len(blob)) if blob else 0.0,
        "columns": per_col,
    }
