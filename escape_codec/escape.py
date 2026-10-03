"""
行式协议转义编解码库（纯字节、纯标准库）。

线路格式（wire format）
-----------------------
- 字段分隔符 FIELD_SEP = b"|"
- 记录分隔符 RECORD_SEP = b"\\n"（一条记录占一行，行内不得再出现裸 \\n）
- 转义引导符 ESCAPE     = b"\\\\"
- 每条记录编码为：field1|field2|...|fieldN + b"\\n"

编码（规范形式 / canonical）
---------------------------
编码器只输出一种写法，保证编码后的字段字节流里不再出现裸的
"|"、"\\n"、"\\r"、"\\\\"：

    \\\\   ->  \\\\\\\\
    |    ->  \\\\|
    \\n   ->  \\\\n
    \\r   ->  \\\\r
    其余  ->  原样字节（含 NUL、高位字节等）

解码（宽容输入 / lenient input）
-------------------------------
读方支持不同来源的多种常见写法，按固定优先级贪心匹配：

    1. 十六进制转义  \\\\xHH           （必须恰好 2 个 hex 位）
    2. Unicode 转义  \\\\uHHHH / \\\\UHHHHHHHH （编码为 UTF-8 字节）
    3. 八进制转义    \\\\0 .. \\\\377   （1~3 位，值必须 <= 255）
    4. 简单转义      \\\\ \\\\  |  n  r  t  a  b  f  v  0  '  "

任何不合法的序列（悬空反斜杠、位数不足/过多、hex 位非法、码点越界
等）都会抛出 EscapeError，错误带 **行号、列号（按字节计）** 以及
出错文本；绝不猜测补全，也不静默丢弃。
"""

from __future__ import annotations

import bisect

FIELD_SEP = ord("|")
RECORD_SEP = ord("\n")
ESCAPE = ord("\\")

# 编码侧：需要转义的字节 -> 规范转义写法
_ENCODE_MAP = {
    ESCAPE: b"\\\\",
    FIELD_SEP: b"\\|",
    ord("\n"): b"\\n",
    ord("\r"): b"\\r",
}

# 解码侧：简单转义（最低优先级的一类）
_SIMPLE_ESCAPES = {
    ord("\\"): b"\\",
    FIELD_SEP: b"|",
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord("a"): b"\a",
    ord("b"): b"\b",
    ord("f"): b"\f",
    ord("v"): b"\v",
    ord("0"): b"\0",
    ord("'"): b"'",
    ord('"'): b'"',
}

_HEX_DIGITS = set(b"0123456789abcdefABCDEF")
_OCT_DIGITS = set(b"01234567")


class EscapeError(ValueError):
    """非法转义序列。

    Attributes:
        line:    行号，从 1 开始
        column:  列号，从 1 开始，按字节计
        offset:  出错引导反斜杠 '\\' 在整条输入中的字节偏移
        message: 人类可读的错误描述
        text:    出错片段（尽量截断到转义序列附近）
    """

    def __init__(self, line: int, column: int, offset: int, message: str, text: bytes = b""):
        self.line = line
        self.column = column
        self.offset = offset
        self.message = message
        self.text = text
        detail = f"line {line}, column {column} (offset {offset}): {message}"
        if text:
            detail += f" near {text!r}"
        super().__init__(detail)


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #

def encode_field(field: bytes | bytearray) -> bytes:
    """编码单个字段，输出中不包含裸的 |、\\n、\\r、\\。"""
    if not isinstance(field, (bytes, bytearray)):
        raise TypeError("field must be bytes")
    out = bytearray()
    for b in field:
        repl = _ENCODE_MAP.get(b)
        if repl is not None:
            out.extend(repl)
        else:
            out.append(b)
    return bytes(out)


def encode_record(fields: list[bytes | bytearray]) -> bytes:
    """编码一条记录：f1|f2|...|fN\\n"""
    return b"|".join(encode_field(f) for f in fields) + b"\n"


def encode(records: list[list[bytes | bytearray]]) -> bytes:
    """编码多条记录。空记录列表无法与空文档区分，显式拒绝。"""
    if records == []:
        raise ValueError("cannot encode zero records (ambiguous with empty document)")
    return b"".join(encode_record(r) for r in records)


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #

def _line_col(line_starts: list[int], offset: int) -> tuple[int, int]:
    """根据记录起始偏移表，把字节偏移换算成 (行号, 列号)，均从 1 开始。"""
    idx = bisect.bisect_right(line_starts, offset) - 1
    line_start = line_starts[idx]
    return idx + 1, offset - line_start + 1


def decode(stream: bytes | bytearray) -> list[list[bytes]]:
    """把线路字节流解码为 records[record][field] -> bytes。

    - 空文档 b"" 等价于一条只含一个空字段的记录。
    - 末尾的 \\n 可有可无（b"a" 与 b"a\\n" 结果相同）。
    - 非法转义抛 EscapeError，带行号/列号。
    """
    if not isinstance(stream, (bytes, bytearray)):
        raise TypeError("stream must be bytes")

    data = bytes(stream)
    line_starts = [0]
    for i, b in enumerate(data):
        if b == RECORD_SEP:
            line_starts.append(i + 1)

    def loc(offset: int) -> tuple[int, int]:
        return _line_col(line_starts, offset)

    def fail(offset: int, message: str, span: int) -> "EscapeError":
        line, column = loc(offset)
        text = data[offset:offset + span]
        return EscapeError(line, column, offset, message, text)

    records: list[list[bytes]] = []
    fields: list[bytes] = []
    buf = bytearray()
    n = len(data)
    i = 0

    def emit_field() -> None:
        fields.append(bytes(buf))
        buf.clear()

    def emit_record() -> None:
        emit_field()
        records.append(fields)  # type: ignore[arg-type]

    while i < n:
        b = data[i]

        if b == FIELD_SEP:
            emit_field()
            i += 1
            continue

        if b == RECORD_SEP:
            emit_record()
            fields = []
            i += 1
            continue

        if b != ESCAPE:
            buf.append(b)
            i += 1
            continue

        # ---- 反斜杠引导，按优先级匹配 ----
        start = i
        if i + 1 >= n:
            raise fail(start, "dangling backslash at end of input: escape sequence is truncated", 1)
        marker = data[i + 1]

        # 优先级 1：\xHH（必须恰好 2 个 hex 位）
        if marker == ord("x"):
            if i + 3 >= n:
                raise fail(start, r"truncated \x escape: expected exactly 2 hex digits", n - start)
            h1, h2 = data[i + 2], data[i + 3]
            if h1 not in _HEX_DIGITS:
                raise fail(start, r"invalid \x escape: expected hex digit, got non-hex character", 3)
            if h2 not in _HEX_DIGITS:
                raise fail(start, r"invalid \x escape: expected hex digit, got non-hex character", 4)
            if i + 4 < n and data[i + 4] in _HEX_DIGITS:
                raise fail(
                    start,
                    r"overlong \x escape: exactly 2 hex digits required, found a 3rd",
                    5,
                )
            buf.append(int(data[i + 2:i + 4], 16))
            i += 4
            continue

        # 优先级 2：\uHHHH / \UHHHHHHHH（按 Unicode 码点解析，再编码为 UTF-8）
        if marker in (ord("u"), ord("U")):
            width = 4 if marker == ord("u") else 8
            tag = r"\u" if width == 4 else r"\U"
            end = i + 2 + width
            if end > n:
                raise fail(
                    start,
                    f"truncated {tag} escape: expected exactly {width} hex digits",
                    n - start,
                )
            digits = data[i + 2:end]
            for pos, d in enumerate(digits):
                if d not in _HEX_DIGITS:
                    raise fail(
                        start,
                        f"invalid {tag} escape: expected hex digit, got non-hex character",
                        2 + pos + 1,
                    )
            if end < n and data[end] in _HEX_DIGITS:
                raise fail(
                    start,
                    f"overlong {tag} escape: exactly {width} hex digits required, found an extra digit",
                    width + 3,
                )
            cp = int(digits, 16)
            if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                raise fail(start, f"invalid Unicode code point U+{cp:04X}", width + 2)
            buf.extend(chr(cp).encode("utf-8"))
            i = end
            continue

        # 优先级 3：八进制 \0..\377（1~3 位，值必须 <= 255）
        if marker in _OCT_DIGITS:
            j = i + 1
            while j < n and j < i + 4 and data[j] in _OCT_DIGITS:
                j += 1
            digits = data[i + 1:j]
            value = int(digits, 8)
            if value > 0xFF:
                raise fail(
                    start,
                    f"octal escape \\{digits.decode()} exceeds byte range (> 255)",
                    len(digits) + 1,
                )
            buf.append(value)
            i = j
            continue

        # 优先级 4：简单转义
        repl = _SIMPLE_ESCAPES.get(marker)
        if repl is None:
            raise fail(start, f"unknown escape sequence: \\{chr(marker)!r}", 2)
        buf.extend(repl)
        i += 2

    # 收尾：流结束时挂着的内容属于最后一个字段
    if data == b"":
        return [[b""]]
    emit_record()
    if data.endswith(b"\n"):
        # 形如 "a\n" 不应产生尾空记录；去掉最后一个多余记录
        records.pop()
    return records


def decode_field(data: bytes) -> bytes:
    """只解码单个字段（不含 |、\\n 结构），非法分隔符同样报错。"""
    records = decode(data)
    if len(records) != 1 or len(records[0]) != 1:
        raise ValueError("input is not a single field")
    return records[0][0]
