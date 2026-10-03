"""行存 -> 列存转换库（仅依赖 Python 3 标准库）。

编码设计
========
1. 空值独立编码：每列带一个 null 位图（bitmap，1 bit/行），空值不进入取值
   序列，也绝不用 0 / "" / False 之类的默认值顶替。
2. 取值编码：对非空值自动选择
   - "rle"   游程编码（连续重复多时最优）
   - "dict"  字典编码（离散取值少、但不连续时最优）
   - "plain" 不编码（都不划算时回退）
3. 类型标签：每个标量带类型标签（i/f/s/b），避免 Python/JSON 里
   1 == True、1 == 1.0 的混淆，保证逐行精确还原。

列存结构（可 json 序列化）::

    {"name": str, "rows": int, "null_bitmap": "base64/自定义hex",
     "null_count": int, "encoding": "rle|dict|plain", "payload": ...}
"""

import json
import zlib

T_INT = "i"
T_FLOAT = "f"
T_STR = "s"
T_BOOL = "b"
_TYPES = (T_INT, T_FLOAT, T_STR, T_BOOL)


class EncodingError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 基础：类型标签 / null 位图
# ---------------------------------------------------------------------------

def _tag(value):
    # 注意：bool 必须先于 int 判断（True/False 也是 int 的子类）
    if isinstance(value, bool):
        return T_BOOL
    if isinstance(value, int):
        return T_INT
    if isinstance(value, float):
        return T_FLOAT
    if isinstance(value, str):
        return T_STR
    raise EncodingError("只支持标量类型 int/float/str/bool，收到 %r" % type(value))


def _tagged(value):
    return [_tag(value), value]


def _untag(item):
    tag, value = item
    if tag not in _TYPES:
        raise EncodingError("未知类型标签 %r" % tag)
    return value


def _bitmap_to_bytes(bits):
    """bool 序列 -> 紧凑位图字节串（每字节 8 行，高位在前）。"""
    out = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        if bit:
            out[i >> 3] |= 0x80 >> (i & 7)
    return bytes(out)


def _bitmap_from_bytes(data, n):
    bits = [False] * n
    for i in range(n):
        if data[i >> 3] & (0x80 >> (i & 7)):
            bits[i] = True
    return bits


# ---------------------------------------------------------------------------
# 列级编码
# ---------------------------------------------------------------------------

def _encode_rle(values):
    """非空值序列 -> [[tag, value, run_length], ...]"""
    runs = []
    for value in values:
        tag = _tag(value)
        if runs and runs[-1][0] == tag and runs[-1][1] == value:
            runs[-1][2] += 1
        else:
            runs.append([tag, value, 1])
    return runs


def _decode_rle(runs):
    out = []
    for tag, value, length in runs:
        if length < 1:
            raise EncodingError("RLE 游程长度非法: %r" % length)
        out.extend([_untag((tag, value))] * length)
    return out


def _encode_dict(values):
    """非空值序列 -> {"dict": [[tag, value], ...], "codes": [int, ...]}"""
    keys = {}
    dictionary = []
    codes = []
    for value in values:
        key = (_tag(value), value)
        if key not in keys:
            keys[key] = len(dictionary)
            dictionary.append(list(key))
        codes.append(keys[key])
    return {"dict": dictionary, "codes": codes}


def _decode_dict(payload):
    dictionary = [_untag(tuple(item)) for item in payload["dict"]]
    return [dictionary[c] for c in payload["codes"]]


def encode_column(name, values):
    """把一列（含 None）编码为列存结构。"""
    null_flags = [v is None for v in values]
    present = [v for v, isnull in zip(values, null_flags) if not isnull]

    n_runs = 0
    for i, value in enumerate(present):
        if i == 0 or value != present[i - 1] or _tag(value) != _tag(present[i - 1]):
            n_runs += 1
    n_distinct = len({(_tag(v), v) for v in present})
    n = len(present)

    # 体积估计：RLE 每项约 12 字节；字典 = 字典项约 12 字节 + 每值 2 字节；
    # plain 每项约 10 字节。只影响选哪种编码，不影响正确性。
    cost_rle = n_runs * 12
    cost_dict = n_distinct * 12 + n * 2
    cost_plain = n * 10

    if present and cost_rle <= cost_dict and cost_rle <= cost_plain:
        encoding, payload = "rle", _encode_rle(present)
    elif present and cost_dict <= cost_plain:
        encoding, payload = "dict", _encode_dict(present)
    else:
        encoding, payload = "plain", [_tagged(v) for v in present]

    return {
        "name": name,
        "rows": len(values),
        "null_count": sum(null_flags),
        "null_bitmap": list(_bitmap_to_bytes(null_flags)),
        "encoding": encoding,
        "payload": payload,
    }


def decode_column(column):
    """列存结构 -> 原始值列表（含 None），逐行精确还原。"""
    n = column["rows"]
    flags = _bitmap_from_bytes(bytes(column["null_bitmap"]), n)
    if len(flags) != n or sum(flags) != column["null_count"]:
        raise EncodingError("null 位图与行数/空值数不一致")

    encoding = column["encoding"]
    if encoding == "rle":
        present = _decode_rle(column["payload"])
    elif encoding == "dict":
        present = _decode_dict(column["payload"])
    elif encoding == "plain":
        present = [_untag(tuple(item)) for item in column["payload"]]
    else:
        raise EncodingError("未知编码 %r" % encoding)

    values = []
    it = iter(present)
    for isnull in flags:
        values.append(None if isnull else next(it))
    trailing = list(it)
    if trailing:
        raise EncodingError("非空值数量多于 null 位图允许的数量")
    return values


# ---------------------------------------------------------------------------
# 表级：行存 <-> 列存
# ---------------------------------------------------------------------------

class ColumnarTable:
    def __init__(self, columns, num_rows, column_order):
        self.columns = {c["name"]: c for c in columns}
        self.num_rows = num_rows
        self.column_order = list(column_order)

    @classmethod
    def from_rows(cls, rows):
        """rows: list[dict]。行中缺失的键按 null 处理。"""
        column_order = []
        for row in rows:
            for key in row:
                if key not in column_order:
                    column_order.append(key)
        columns = []
        for name in column_order:
            values = [row.get(name) for row in rows]
            columns.append(encode_column(name, values))
        return cls(columns, len(rows), column_order)

    def decode_column(self, name):
        return decode_column(self.columns[name])

    def to_rows(self):
        """还原为行存；还原出的每一行键齐全（null 位置为 None）。"""
        decoded = {name: self.decode_column(name) for name in self.column_order}
        return [
            {name: decoded[name][i] for name in self.column_order}
            for i in range(self.num_rows)
        ]

    def to_json(self):
        return json.dumps(
            {"num_rows": self.num_rows,
             "column_order": self.column_order,
             "columns": [self.columns[n] for n in self.column_order]},
            ensure_ascii=False, separators=(",", ":"), allow_nan=True,
        )

    @classmethod
    def from_json(cls, text):
        obj = json.loads(text)
        return cls(obj["columns"], obj["num_rows"], obj["column_order"])


def normalize_rows(rows):
    """行对拍前统一：补齐缺失键为 None，按首次出现顺序排列列。"""
    order = []
    for row in rows:
        for key in row:
            if key not in order:
                order.append(key)
    return [{key: row.get(key) for key in order} for row in rows], order


# ---------------------------------------------------------------------------
# 查询（行存 / 列存共用同一套过滤语义，便于逐行对拍）
# ---------------------------------------------------------------------------

OPS = ("==", "!=", "<", "<=", ">", ">=", "is_null", "not_null")


def _match(value, op, arg):
    if op == "is_null":
        return value is None
    if op == "not_null":
        return value is not None
    if value is None:
        return False  # SQL 风格：null 不参与比较
    # 严格区分 bool 与 int，避免 True == 1 导致结果歧义
    if isinstance(value, bool) != isinstance(arg, bool):
        return False
    if op == "==":
        return value == arg
    if op == "!=":
        return value != arg
    if op in ("<", "<=", ">", ">="):
        try:
            if op == "<":
                return value < arg
            if op == "<=":
                return value <= arg
            if op == ">":
                return value > arg
            return value >= arg
        except TypeError:
            return False
    raise EncodingError("未知比较符 %r" % op)


def _apply_filter(row, filters):
    for col, op, arg in filters:
        if not _match(row.get(col), op, arg):
            return False
    return True


def query_rows(rows, select, filters=()):
    """行存查询：select 为列名列表，filters 为 [(列, op, 参数), ...]。"""
    return [
        [row.get(col) for col in select]
        for row in rows
        if _apply_filter(row, filters)
    ]


def query_columnar(table, select, filters=()):
    """列存查询：只解码涉及的列，过滤后做投影。"""
    needed = list(select)
    for col, _, _ in filters:
        if col not in needed:
            needed.append(col)
    decoded = {name: table.decode_column(name) for name in needed}
    result = []
    for i in range(table.num_rows):
        row = {name: decoded[name][i] for name in needed}
        if _apply_filter(row, filters):
            result.append([decoded[col][i] for col in select])
    return result


# ---------------------------------------------------------------------------
# 体积 / 压缩率测量
# ---------------------------------------------------------------------------

def _row_bytes(rows):
    return len(json.dumps(rows, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=True).encode("utf-8"))


def measure(rows):
    """返回行存与列存（原始 + zlib 后）的体积与压缩率。

    ratio = 列存体积 / 行存体积，越小越好；saving = 1 - ratio。
    """
    rows_norm, _ = normalize_rows(rows)
    table = ColumnarTable.from_rows(rows)
    rb = _row_bytes(rows_norm)
    cb = len(table.to_json().encode("utf-8"))
    rbz = len(zlib.compress(_row_dump(rows_norm), 6))
    cbz = len(zlib.compress(table.to_json().encode("utf-8"), 6))
    return {
        "rows": len(rows),
        "row_bytes": rb,
        "columnar_bytes": cb,
        "ratio": cb / rb if rb else 0.0,
        "saving": 1 - cb / rb if rb else 0.0,
        "row_zlib_bytes": rbz,
        "columnar_zlib_bytes": cbz,
        "zlib_ratio": cbz / rbz if rbz else 0.0,
    }


def _row_dump(rows_norm):
    return json.dumps(rows_norm, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=True).encode("utf-8")
