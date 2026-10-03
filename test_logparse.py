"""logparse 自测（python3 test_logparse.py 或 python3 -m unittest -v）。"""

from __future__ import annotations

import os
import unittest

import logparse
import make_samples

ROOT = os.path.dirname(os.path.abspath(__file__))
CORPUS_DIR = os.path.join(ROOT, "samples", "corpus")
INVALID_DIR = os.path.join(ROOT, "samples", "invalid")


def frame(body: bytes) -> bytes:
    return str(len(body)).encode("ascii") + b" " + body


class PriorityTests(unittest.TestCase):
    def test_facility_and_severity(self):
        msg = logparse.parse(b"<13>web01 hello")
        self.assertEqual(msg.pri, 13)
        self.assertEqual(msg.facility, 1)
        self.assertEqual(msg.facility_name, "user")
        self.assertEqual(msg.severity, 5)
        self.assertEqual(msg.severity_name, "notice")

    def test_boundaries(self):
        for pri in (0, 1, 190, 191):
            msg = logparse.parse(f"<{pri}>h m".encode())
            self.assertEqual(msg.pri, pri)
        zero = logparse.parse(b"<0>h m")
        self.assertEqual((zero.facility, zero.severity), (0, 0))
        top = logparse.parse(b"<191>h m")
        self.assertEqual((top.facility, top.severity), (23, 7))
        self.assertEqual(top.facility_name, "local7")
        self.assertEqual(top.severity_name, "debug")

    def test_out_of_range_must_raise_not_truncate(self):
        for raw in (b"<192>h m", b"<255>h m", b"<99999>h m"):
            with self.subTest(raw=raw), self.assertRaises(logparse.LogParseError):
                logparse.parse(raw)

    def test_non_canonical_pri_rejected(self):
        with self.assertRaises(logparse.LogParseError):
            logparse.parse(b"<013>h m")  # 前导零会破坏逐字节往返


class TraditionalTests(unittest.TestCase):
    def test_pure_traditional(self):
        raw = b"<13>web01 nginx 404 burst detected"
        msg = logparse.parse(raw)
        self.assertFalse(msg.framed)
        self.assertEqual(msg.host, "web01")
        self.assertIsNone(msg.structured)
        self.assertIsNone(msg.structured_map)
        self.assertEqual(msg.message, "nginx 404 burst detected")
        self.assertEqual(msg.rebuild(), raw)


class StructuredTests(unittest.TestCase):
    def test_pure_structured_empty_message(self):
        body = b'<134>db01 [app="postgres" app="pgbouncer" env="prod"]'
        raw = frame(body)
        msg = logparse.parse(raw)
        self.assertTrue(msg.framed)
        self.assertEqual(msg.message, "")
        self.assertEqual([e.key for e in msg.structured], ["app", "app", "env"])
        self.assertEqual(
            [(e.order, e.occurrence) for e in msg.structured],
            [(0, 0), (1, 1), (2, 0)],
        )
        self.assertEqual(
            msg.structured_map,
            {"app": ["postgres", "pgbouncer"], "env": ["prod"]},
        )
        self.assertEqual(msg.rebuild(), raw)

    def test_escapes_roundtrip(self):
        body = b'<165>fw01 [rule="a\\"b\\\\c\\]d" note="line\\nbreak\\tend"] tcp'
        raw = frame(body)
        msg = logparse.parse(raw)
        self.assertEqual(msg.structured[0].value, 'a"b\\c]d')
        self.assertEqual(msg.structured[1].value, "line\nbreak\tend")
        self.assertEqual(msg.rebuild(), raw)

    def test_empty_sd_is_distinct_from_no_sd(self):
        raw = b"<0>edge01 [] boot ok"
        msg = logparse.parse(raw)
        self.assertEqual(msg.structured, [])
        self.assertEqual(msg.structured_map, {})
        self.assertEqual(msg.message, "boot ok")
        self.assertEqual(msg.rebuild(), raw)

    def test_mixed_message_after_sd(self):
        raw = frame(b'<14>host [k="v"] free text with [brackets]')
        msg = logparse.parse(raw)
        self.assertEqual(msg.message, "free text with [brackets]")
        self.assertEqual(msg.rebuild(), raw)


class NonAsciiTests(unittest.TestCase):
    def test_utf8_value_and_message(self):
        body = '<190>cache01 [user="张三" city="北京"] 缓存刷新完成'.encode("utf-8")
        raw = frame(body)
        msg = logparse.parse(raw)
        self.assertEqual(msg.structured_map["user"], ["张三"])
        self.assertEqual(msg.structured_map["city"], ["北京"])
        self.assertEqual(msg.message, "缓存刷新完成")
        self.assertEqual(msg.rebuild(), raw)


class LengthFrameTests(unittest.TestCase):
    def test_bad_declared_length(self):
        with self.assertRaisesRegex(logparse.LogParseError, "长度声明错误"):
            logparse.parse(b"58 <13>web01 short body")

    def test_length_off_by_one_with_utf8(self):
        body = '<13>cache01 [city="北京"] ok'.encode("utf-8")
        raw = str(len(body) + 1).encode() + b" " + body
        with self.assertRaisesRegex(logparse.LogParseError, "长度声明错误"):
            logparse.parse(raw)

    def test_leading_zero_length_rejected(self):
        with self.assertRaises(logparse.LogParseError):
            logparse.parse(b"012 <13>web01 ok")


class MalformedTests(unittest.TestCase):
    def test_bad_escape(self):
        with self.assertRaisesRegex(logparse.LogParseError, "非法转义"):
            logparse.parse(b'<13>h [k="a\\xb"] x')

    def test_unescaped_bracket(self):
        with self.assertRaises(logparse.LogParseError):
            logparse.parse(b'<13>h [k="a]b"] x')

    def test_unterminated_value(self):
        with self.assertRaises(logparse.LogParseError):
            logparse.parse(b'<13>h [k="abc] x')

    def test_missing_gt(self):
        with self.assertRaises(logparse.LogParseError):
            logparse.parse(b"<13h x")

    def test_empty_and_type(self):
        with self.assertRaises(logparse.LogParseError):
            logparse.parse(b"")
        with self.assertRaises(TypeError):
            logparse.parse("<13>h m")


class CorpusRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make_samples.main()

    def test_every_valid_corpus_roundtrips_byte_exact(self):
        for name in sorted(os.listdir(CORPUS_DIR)):
            with self.subTest(name=name):
                with open(os.path.join(CORPUS_DIR, name), "rb") as fh:
                    raw = fh.read()
                msg = logparse.parse(raw)
                self.assertEqual(
                    msg.rebuild(), raw,
                    msg="重建报文与原报文存在字节差异",
                )

    def test_every_invalid_corpus_is_rejected(self):
        for name in sorted(os.listdir(INVALID_DIR)):
            with self.subTest(name=name):
                with open(os.path.join(INVALID_DIR, name), "rb") as fh:
                    raw = fh.read()
                with self.assertRaises(logparse.LogParseError):
                    logparse.parse(raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
