"""行式协议转义编解码库（仅依赖标准库）。

格式约定：
  - 记录（行）之间以 LF 分隔，字段之间以 SEP（默认 '|'）分隔。
  - 编码器保证输出中不再出现原始的分隔符 / 换行 / 反斜杠字节。
  - 解码器支持多种常见转义写法，按优先级（最长匹配优先）：
      1. \\xHH   两位十六进制字节（不区分大小写）
      2. \\\\     反斜杠自身
      3. \\n \\r \\t \\0  常见控制字符
      4. \\<SEP>  被转义的分隔符（如 \\|）
  - 任何其它序列均为非法转义，抛出 EscapeError，携带 1-based 行号与
    字节列号（列指向反斜杠本身），不猜测、不补全、不丢弃。
"""

from __future__ import annotations

SEPARATOR = 0x7C  # '|'
RECORD_SEP = 0x0A  # '\n'
BACKSLASH = 0x5C  # '\\'

_HEX_DIGITS = b"0123456789abcdefABCDEF"


class EscapeError(ValueError):
    """非法转义序列。line / column 均为 1-based，column 指向反斜杠所在字节列。"""

    def __init__(self, message: str, line: int, column: int) -> None:
        self.line = line
        self.column = column
        super().__init__(f"line {line}, column {column}: {message}")


def _simple_escape_table(separator: int) -> dict:
    return {
        BACKSLASH: BACKSLASH,   # \\ -> '\'
        ord("n"): 0x0A,         # \n -> LF
        ord("r"): 0x0D,         # \r -> CR
        ord("t"): 0x09,         # \t -> TAB
        ord("0"): 0x00,         # \0 -> NUL
        separator: separator,   # \| -> '|'
    }


def _decode_escape(data: bytes, i: int, line: int, column: int, table: dict):
    """data[i] 为反斜杠，返回 (解码出的字节, 消耗的输入长度)。失败抛 EscapeError。"""
    n = len(data)
    if i + 1 >= n:
        raise EscapeError("truncated escape: backslash at end of input", line, column)
    nxt = data[i + 1]
    # 优先级 1：\xHH（最长匹配优先）
    if nxt == ord("x"):
        if i + 3 >= n:
            raise EscapeError(
                "truncated escape: '\\x' requires exactly 2 hex digits", line, column
            )
        hi, lo = data[i + 2], data[i + 3]
        if hi not in _HEX_DIGITS or lo not in _HEX_DIGITS:
            raise EscapeError(
                "invalid escape: '\\x' must be followed by 2 hex digits, "
                f"got {bytes([hi, lo])!r}",
                line,
                column,
            )
        value = int(bytes([hi, lo]), 16)
        return bytes([value]), 4
    # 优先级 2-4：单字符简单转义
    if nxt in table:
        return bytes([table[nxt]]), 2
    raise EscapeError(
        f"invalid escape sequence: '\\{chr(nxt)}' (0x{nxt:02x}) is not a known escape",
        line,
        column,
    )


def encode_field(raw: bytes, separator: int = SEPARATOR) -> bytes:
    """编码单个字段，保证输出中不出现原始分隔符 / 换行 / 反斜杠。"""
    out = bytearray()
    for b in raw:
        if b == BACKSLASH:
            out += b"\\\\"
        elif b == separator:
            out += b"\\" + bytes([separator])
        elif b == 0x0A:
            out += b"\\n"
        elif b == 0x0D:
            out += b"\\r"
        elif b == 0x09:
            out += b"\\t"
        elif b == 0x00:
            out += b"\\0"
        elif b < 0x20 or b == 0x7F:
            out += b"\\x%02x" % b
        else:
            out.append(b)
    return bytes(out)


def decode_field(data: bytes, separator: int = SEPARATOR,
                 line: int = 1, column_offset: int = 0) -> bytes:
    """解码单个字段片段（不应包含原始分隔符 / 换行）。"""
    table = _simple_escape_table(separator)
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        if data[i] == BACKSLASH:
            decoded, consumed = _decode_escape(
                data, i, line, column_offset + i + 1, table
            )
            out += decoded
            i += consumed
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def encode_record(fields, separator: int = SEPARATOR) -> bytes:
    """编码一条记录（字段序列）为一行（不含换行）。"""
    sep = bytes([separator])
    return sep.join(encode_field(f, separator) for f in fields)


def decode_record(line_bytes: bytes, separator: int = SEPARATOR,
                  line: int = 1) -> list:
    """解码一行（不含换行）为字段列表，错误携带行号与行内列号。

    单遍扫描：只有未转义的分隔符才切分字段（转义序列 \\| 中的 '|' 不算）。
    """
    table = _simple_escape_table(separator)
    fields = []
    field = bytearray()
    i, n = 0, len(line_bytes)
    while i < n:
        b = line_bytes[i]
        if b == BACKSLASH:
            decoded, consumed = _decode_escape(line_bytes, i, line, i + 1, table)
            field += decoded
            i += consumed
        elif b == separator:
            fields.append(bytes(field))
            field.clear()
            i += 1
        else:
            field.append(b)
            i += 1
    fields.append(bytes(field))
    return fields


def encode_payload(records, separator: int = SEPARATOR) -> bytes:
    """编码整个负载：每条记录一行，以 LF 结尾。"""
    return b"".join(
        encode_record(rec, separator) + b"\n" for rec in records
    )


def decode_payload(payload: bytes, separator: int = SEPARATOR) -> list:
    """解码整个负载为记录列表。非法转义抛出带行号/列号的 EscapeError。"""
    lines = payload.split(b"\n")
    # 末尾 LF 是记录终止符，不是一条额外的空记录
    if lines and lines[-1] == b"":
        lines.pop()
    records = []
    for lineno, line_bytes in enumerate(lines, start=1):
        records.append(decode_record(line_bytes, separator, lineno))
    return records
