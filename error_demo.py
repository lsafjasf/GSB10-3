"""非法转义的行号/列号定位样例（仅标准库）。

运行: python3 error_demo.py
"""

from escape_codec import EscapeError, decode_payload

SAMPLES = [
    (b"name|value\nhello|wor\\qld\n", "未知转义符 \\q"),
    (b"line1\nline2\nabc\\\n", "行末残缺转义（孤立反斜杠）"),
    (b"ok\\x41|bad\\x4\n", "十六进制转义被截断"),
    (b"a|b|c\\xzz\n", "十六进制数字非法"),
    (b"\\n is fine\nbut \\@ is not\n", "错误位于第二行"),
]


def show(payload: bytes, note: str):
    print("=" * 64)
    print("样例:", note)
    for lineno, text in enumerate(payload.split(b"\n"), start=1):
        if text:
            print("  %d | %s" % (lineno, text.decode("latin-1")))
    try:
        decode_payload(payload)
    except EscapeError as err:
        line_text = payload.split(b"\n")[err.line - 1].decode("latin-1")
        caret = " " * (4 + len(str(err.line)) + err.column - 1) + "^"
        print("  错误: %s" % err)
        print("  %d | %s" % (err.line, line_text))
        print(caret)
    else:
        print("  (未触发错误——这不是预期的)")


if __name__ == "__main__":
    for payload, note in SAMPLES:
        show(payload, note)
