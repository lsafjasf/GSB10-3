"""边界用例与错误定位自测（标准库 unittest）。

运行: python3 test_escape_codec.py -v
"""

import unittest

from escape_codec import (
    EscapeError,
    decode_field,
    decode_payload,
    decode_record,
    encode_field,
    encode_payload,
    encode_record,
)


def roundtrip_field(raw: bytes) -> bytes:
    return decode_field(encode_field(raw))


def roundtrip_payload(records):
    return decode_payload(encode_payload(records))


class TestRoundTrip(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(roundtrip_field(b"hello"), b"hello")

    def test_all_specials(self):
        raw = b"\\|\n\r\t\x00\x01\x1f\x7f"
        self.assertEqual(roundtrip_field(raw), raw)

    def test_all_256_bytes(self):
        raw = bytes(range(256))
        self.assertEqual(roundtrip_field(raw), raw)

    def test_utf8_passthrough(self):
        raw = "你好，世界｜全角分隔符".encode("utf-8")
        self.assertEqual(roundtrip_field(raw), raw)

    def test_encoded_output_has_no_raw_separator_or_newline(self):
        raw = b"|\n\r\\" * 100
        enc = encode_field(raw)
        self.assertNotIn(b"|", enc.replace(b"\\|", b""))
        self.assertNotIn(b"\n", enc)
        self.assertNotIn(b"\r", enc)


class TestEdgeCases(unittest.TestCase):
    def test_empty_field(self):
        self.assertEqual(encode_field(b""), b"")
        self.assertEqual(decode_field(b""), b"")
        # 记录 "||" -> 三个空字段
        self.assertEqual(decode_record(b"||"), [b"", b"", b""])
        self.assertEqual(roundtrip_payload([[b"", b"", b""]]), [[b"", b"", b""]])

    def test_field_of_only_separators(self):
        raw = b"|||||"
        enc = encode_field(raw)
        self.assertEqual(enc, b"\\|\\|\\|\\|\\|")
        self.assertEqual(roundtrip_field(raw), raw)
        # 整行就是一个全分隔符字段，不能被误切成多个字段
        self.assertEqual(decode_record(enc), [raw])

    def test_very_long_escape_runs(self):
        # 一万个反斜杠 -> 两万个转义字符
        raw = b"\\" * 10000
        enc = encode_field(raw)
        self.assertEqual(len(enc), 20000)
        self.assertEqual(roundtrip_field(raw), raw)
        # 超长 \xHH 序列
        raw2 = bytes(range(0x20)) * 500
        self.assertEqual(roundtrip_field(raw2), raw2)
        # 超长分隔符字段
        raw3 = b"|" * 100000
        self.assertEqual(roundtrip_field(raw3), raw3)

    def test_nested_escapes(self):
        # 数据本身是字面量 "\n"（反斜杠+字母n），编码后必须变成 \\n
        raw = b"\\n"
        enc = encode_field(raw)
        self.assertEqual(enc, b"\\\\n")
        self.assertEqual(roundtrip_field(raw), raw)
        # 解码 \\n 得到字面量 "\n" 而不是换行
        self.assertEqual(decode_field(b"\\\\n"), b"\\n")
        # 数据本身是字面量 "\x41"
        raw2 = b"\\x41"
        self.assertEqual(roundtrip_field(raw2), raw2)
        self.assertEqual(encode_field(raw2), b"\\\\x41")
        # 三层嵌套：字面量 "\\n"
        raw3 = b"\\\\n"
        self.assertEqual(roundtrip_field(raw3), raw3)
        # \x5c 是反斜杠的十六进制写法，解码器也要认
        self.assertEqual(decode_field(b"\\x5c"), b"\\")
        self.assertEqual(decode_field(b"\\x5C"), b"\\")

    def test_decoder_accepts_multiple_escape_styles(self):
        # 同一个字节 LF 的多种写法都要能解
        self.assertEqual(decode_field(b"\\n"), b"\n")
        self.assertEqual(decode_field(b"\\x0a"), b"\n")
        self.assertEqual(decode_field(b"\\x0A"), b"\n")
        # 优先级：\xHH 按最长匹配优先，\x41 -> 'A' 而不是报错
        self.assertEqual(decode_field(b"\\x41\\x42"), b"AB")
        # 常见控制字符写法
        self.assertEqual(decode_field(b"\\r\\t\\0\\\\\\|"), b"\r\t\x00\\|")

    def test_empty_payload_and_trailing_newline(self):
        self.assertEqual(decode_payload(b""), [])
        self.assertEqual(roundtrip_payload([]), [])
        # 末尾 LF 是终止符，不多出一条空记录
        self.assertEqual(decode_payload(b"a\n"), [[b"a"]])
        # 但中间的空行是一条含一个空字段的记录
        self.assertEqual(decode_payload(b"a\n\nb\n"), [[b"a"], [b""], [b"b"]])


class TestErrorLocation(unittest.TestCase):
    def assert_error(self, payload, line, column):
        with self.assertRaises(EscapeError) as ctx:
            decode_payload(payload)
        self.assertEqual(ctx.exception.line, line, payload)
        self.assertEqual(ctx.exception.column, column, payload)

    def test_unknown_escape(self):
        self.assert_error(b"ab\\qcd\n", 1, 3)

    def test_unknown_escape_second_line(self):
        self.assert_error(b"good|fields\nok\nxy\\q\n", 3, 3)

    def test_column_accounts_for_fields(self):
        # 错误在第二个字段里，列号是整行内的绝对字节列
        self.assert_error(b"abc|de\\qf\n", 1, 7)

    def test_trailing_backslash(self):
        self.assert_error(b"abc\\\n", 1, 4)

    def test_truncated_hex(self):
        self.assert_error(b"\\x4\n", 1, 1)
        self.assert_error(b"zz\\x\n", 1, 3)

    def test_invalid_hex_digits(self):
        self.assert_error(b"\\xzz\n", 1, 1)
        self.assert_error(b"\\x4g\n", 1, 1)

    def test_error_does_not_silently_drop(self):
        # 非法转义绝不猜测补全或丢弃：必须抛异常
        for bad in (b"\\q", b"\\", b"\\x", b"\\x1", b"\\xgg", b"a\\ b"):
            with self.assertRaises(EscapeError, msg=bad):
                decode_payload(bad + b"\n")


if __name__ == "__main__":
    unittest.main()
