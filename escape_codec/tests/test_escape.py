"""escape_codec 单元测试：python3 -m unittest discover -s tests -v"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from escape import (
    EscapeError,
    decode,
    decode_field,
    encode,
    encode_field,
    encode_record,
)


class TestEncode(unittest.TestCase):
    def test_plain_bytes_pass_through(self):
        self.assertEqual(encode_field(b"hello world"), b"hello world")

    def test_empty_field(self):
        self.assertEqual(encode_field(b""), b"")
        self.assertEqual(encode_record([b""]), b"\n")
        self.assertEqual(encode_record([b"", b""]), b"|\n")

    def test_delimiters_are_escaped(self):
        self.assertEqual(encode_field(b"a|b"), b"a\\|b")
        self.assertEqual(encode_field(b"a\nb"), b"a\\nb")
        self.assertEqual(encode_field(b"a\rb"), b"a\\rb")
        self.assertEqual(encode_field(b"a\\b"), b"a\\\\b")

    def test_field_all_delimiters(self):
        self.assertEqual(encode_field(b"||||"), b"\\|\\|\\|\\|")
        self.assertEqual(encode_field(b"\n\n\n"), b"\\n\\n\\n")
        self.assertEqual(encode_field(b"|\n|\n"), b"\\|\\n\\|\\n")

    def test_no_raw_delimiter_in_output(self):
        # 编码不变量：字段编码结果里不得出现裸 |、\n、\r、\
        raw = bytes(range(256))
        out = encode_field(raw)
        # 剥掉所有规范转义对后，不得残留任何分隔符/反斜杠
        stripped = out
        for esc in (b"\\\\", b"\\|", b"\\n", b"\\r"):
            stripped = stripped.replace(esc, b"")
        for bad in b"|\n\r\\":
            self.assertNotIn(bytes([bad]), stripped)

    def test_record_and_document_layout(self):
        self.assertEqual(encode_record([b"a", b"b"]), b"a|b\n")
        self.assertEqual(encode([[b"a"], [b"b"]]), b"a\nb\n")

    def test_zero_records_rejected(self):
        with self.assertRaises(ValueError):
            encode([])


class TestDecodeNotations(unittest.TestCase):
    """解码器支持的多种写法 + 优先级。"""

    def test_simple_escapes(self):
        self.assertEqual(decode_field(b"\\|"), b"|")
        self.assertEqual(decode_field(b"\\n"), b"\n")
        self.assertEqual(decode_field(b"\\r"), b"\r")
        self.assertEqual(decode_field(b"\\t"), b"\t")
        self.assertEqual(decode_field(b"\\\\"), b"\\")
        self.assertEqual(decode_field(b"\\0"), b"\0")
        self.assertEqual(decode_field(b"\\a\\b\\f\\v"), b"\a\b\f\v")
        self.assertEqual(decode_field(b"\\'\\\""), b"'\"")

    def test_hex_escape(self):
        self.assertEqual(decode_field(b"\\x41\\x42"), b"AB")
        self.assertEqual(decode_field(b"\\x7c"), b"|")      # 小写 hex
        self.assertEqual(decode_field(b"\\x7C"), b"|")      # 大写 hex
        self.assertEqual(decode_field(b"\\x00"), b"\0")

    def test_unicode_escapes(self):
        self.assertEqual(decode_field(b"\\u0041"), b"A")
        self.assertEqual(decode_field(b"\\u00e9"), "é".encode("utf-8"))
        self.assertEqual(decode_field(b"\\U0001F600"), "😀".encode("utf-8"))

    def test_octal_escapes(self):
        self.assertEqual(decode_field(b"\\101"), b"A")      # 3 位
        self.assertEqual(decode_field(b"\\52"), b"*")       # 2 位
        self.assertEqual(decode_field(b"\\7"), b"\a")       # 1 位
        self.assertEqual(decode_field(b"\\377"), b"\xff")

    def test_priority_octal_over_simple(self):
        # \0 后面还有八进制数字时，按八进制（更长）优先匹配
        self.assertEqual(decode_field(b"\\012"), b"\n")
        self.assertEqual(decode_field(b"\\01"), b"\x01")

    def test_mixed_notations_in_one_field(self):
        self.assertEqual(
            decode_field(b"\\x41\\102\\u0043Z"),
            b"ABCZ",
        )

    def test_nested_escape(self):
        # 原始字节是 '\' + '|'：编码后 \ 自身被转义，解码需先还原文本层
        self.assertEqual(decode_field(b"\\\\\\|"), b"\\|")
        # 原始内容是字面文本 "\x41"（4 字节），编码后是 \\x41
        self.assertEqual(decode_field(b"\\\\x41"), b"\\x41")
        # 三重嵌套：原始 "\\\\" -> 编码 "\\\\\\\\"
        self.assertEqual(decode_field(b"\\\\\\\\"), b"\\\\")

    def test_structure_split(self):
        self.assertEqual(decode(b"a|b\nc|d\n"), [[b"a", b"b"], [b"c", b"d"]])
        self.assertEqual(decode(b"a|b"), [[b"a", b"b"]])          # 末尾无换行
        self.assertEqual(decode(b""), [[b""]])                      # 空文档
        self.assertEqual(decode(b"\n"), [[b""]])                    # 空记录
        self.assertEqual(decode(b"|\n"), [[b"", b""]])              # 两个空字段
        self.assertEqual(decode(b"a\\|b|c"), [[b"a|b", b"c"]])      # 转义分隔符不参与切分
        self.assertEqual(decode(b"\\x7c|\\n"), [[b"|", b"\n"]])     # hex 形式的分隔符同理

    def test_multiline_positions_of_valid_data(self):
        self.assertEqual(decode(b"ab\ncd\nef"), [[b"ab"], [b"cd"], [b"ef"]])


class TestErrors(unittest.TestCase):
    """非法转义必须带行号/列号，不允许猜测或丢弃。"""

    def assertErr(self, data, line, column, msg_part):
        with self.assertRaises(EscapeError) as ctx:
            decode(data)
        e = ctx.exception
        self.assertEqual(e.line, line, f"{data!r}: line")
        self.assertEqual(e.column, column, f"{data!r}: column")
        self.assertIn(msg_part, e.message)

    def test_dangling_backslash(self):
        self.assertErr(b"abc\\", 1, 4, "dangling backslash")

    def test_unknown_escape(self):
        self.assertErr(b"ab\\qcd", 1, 3, "unknown escape")
        self.assertErr(b"\\q", 1, 1, "unknown escape")

    def test_truncated_hex(self):
        self.assertErr(b"\\x", 1, 1, "truncated")
        self.assertErr(b"\\x4", 1, 1, "truncated")

    def test_invalid_hex_digit(self):
        self.assertErr(b"\\xzz", 1, 1, "hex digit")
        self.assertErr(b"\\x4z", 1, 1, "hex digit")

    def test_overlong_hex(self):
        # 超长转义：\x 恰好 2 位，第 3 个 hex 位属于错误而非静默截断
        self.assertErr(b"\\x414", 1, 1, "overlong")

    def test_truncated_unicode(self):
        self.assertErr(b"\\u004", 1, 1, "truncated")
        self.assertErr(b"\\U0001F60", 1, 1, "truncated")

    def test_invalid_unicode_codepoint(self):
        self.assertErr(b"\\U00110000", 1, 1, "code point")
        self.assertErr(b"\\uD800", 1, 1, "code point")

    def test_octal_overflow(self):
        self.assertErr(b"\\400", 1, 1, "exceeds byte range")

    def test_error_position_on_later_lines(self):
        # 第 3 行第 3 列出错
        self.assertErr(b"ab\ncd\nxy\\q", 3, 3, "unknown escape")
        # 出错行内列号按字节计
        self.assertErr(b"ok|fine\nzz\\x", 2, 3, "truncated")

    def test_error_carries_text(self):
        with self.assertRaises(EscapeError) as ctx:
            decode(b"hello\\qworld")
        self.assertTrue(ctx.exception.text.startswith(b"\\q"))


class TestRoundTrip(unittest.TestCase):
    def test_all_single_bytes(self):
        for v in range(256):
            raw = bytes([v])
            self.assertEqual(decode_field(encode_field(raw)), raw)

    def test_all_byte_pairs(self):
        for a in range(256):
            for b in range(256):
                raw = bytes([a, b])
                self.assertEqual(decode_field(encode_field(raw)), raw)

    def test_boundary_cases(self):
        cases = [
            [[b""]],                          # 空字段
            [[b"", b"", b""]],                # 连续空字段
            [[b"||||"]],                      # 字段全是分隔符
            [[b"\n\n\n"]],                    # 字段全是换行
            [[b"\\\\"]],                      # 字段全是反斜杠
            [[b"\\|"]],                       # 嵌套转义的源头
            [[b"\\x41"]],                     # 内容本身像转义序列
            [[bytes(range(256))]],            # 全字节表
            [[b"a" * 100000]],                # 超长字段
            [[b"\x00" * 1000]],               # 大量 NUL
            [[b"x"] * 200],                   # 超多字段
            [["é😀".encode("utf-8")]],       # 多字节 UTF-8 原样透传
            [[b""], [b""], [b""]],            # 多条空记录
        ]
        for records in cases:
            with self.subTest(records=records[:1]):
                self.assertEqual(decode(encode(records)), records)


if __name__ == "__main__":
    unittest.main()
