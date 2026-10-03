# -*- coding: utf-8 -*-
"""icmp_error 的自测（python3 -m unittest -v）。

覆盖：
  - 奇数长度负载的构造/解析往返
  - 全零负载（校验和 0xffff，即 -0）
  - 校验和被篡改（报错必须同时给出期望/实际值）
  - 长度与声明不符（截断、附加、伪造长度字段）
  - RFC 1071 / RFC 791 标准向量
  - 协议字段自洽性与字段范围校验
"""

from __future__ import annotations

import struct
import unittest

import icmp_error
from icmp_error import (
    ChecksumMismatchError,
    LengthMismatchError,
    ProtocolError,
    build_error_message,
    internet_checksum,
    parse_error_message,
    verify_checksum,
)


class ChecksumStandardVectors(unittest.TestCase):
    def test_rfc1071_vector(self):
        data = bytes.fromhex("0001f203f4f5f6f7")
        self.assertEqual(internet_checksum(data), 0x220D)

    def test_rfc791_ipv4_header_vector(self):
        data = bytes.fromhex(
            "450000730000400040110000c0a80001c0a800c7"
        )
        self.assertEqual(internet_checksum(data), 0xB861)

    def test_odd_length_payload(self):
        self.assertEqual(internet_checksum(b"abcde"), 0xD638)

    def test_all_zero_checksum_is_ffff(self):
        # 全零数据反码求和为 0，取反得 0xffff（-0）
        for n in (1, 2, 7, 8, 15, 16):
            self.assertEqual(internet_checksum(b"\x00" * n), 0xFFFF)

    def test_single_byte_ff(self):
        self.assertEqual(internet_checksum(b"\xff"), 0x00FF)

    def test_verify_full_message_sums_to_zero(self):
        msg = build_error_message(3, 3, b"abcde", protocol=icmp_error.PROTO_UDP)
        self.assertTrue(verify_checksum(msg))


class BuildParseRoundTrip(unittest.TestCase):
    def test_odd_length_payload_roundtrip(self):
        payload = b"\xde\xad\xbe\xef\x01"  # 5 字节，奇数
        msg = build_error_message(3, 3, payload, protocol=icmp_error.PROTO_UDP)
        self.assertEqual(len(msg), 8 + len(payload))
        self.assertEqual(struct.unpack("!H", msg[4:6])[0], len(msg))
        parsed = parse_error_message(msg)
        self.assertEqual(parsed.type, 3)
        self.assertEqual(parsed.code, 3)
        self.assertEqual(parsed.protocol, 17)
        self.assertEqual(parsed.length, len(msg))
        self.assertEqual(parsed.payload, payload)

    def test_all_zero_payload_roundtrip(self):
        payload = b"\x00" * 8
        msg = build_error_message(3, 1, payload)
        # 全零负载时校验和只取决于头部，整体仍应可通过校验
        parsed = parse_error_message(msg)
        self.assertEqual(parsed.payload, payload)
        self.assertEqual(parsed.checksum, struct.unpack("!H", msg[2:4])[0])

    def test_empty_payload(self):
        msg = build_error_message(11, 0)
        self.assertEqual(len(msg), 8)
        parsed = parse_error_message(msg)
        self.assertEqual(parsed.payload, b"")

    def test_length_field_self_consistent(self):
        for payload in (b"", b"\x01", b"x" * 255, b"y" * 1000):
            msg = build_error_message(5, 0, payload, protocol=icmp_error.PROTO_TCP)
            declared = struct.unpack("!H", msg[4:6])[0]
            self.assertEqual(declared, len(msg))
            self.assertEqual(parse_error_message(msg).payload, payload)


class TamperedChecksum(unittest.TestCase):
    def test_payload_bit_flip_reports_expected_and_actual(self):
        msg = bytearray(build_error_message(3, 0, b"\x00" * 9))
        msg[-1] ^= 0x01  # 篡改随包数据 1 个比特

        with self.assertRaises(ChecksumMismatchError) as ctx:
            parse_error_message(bytes(msg))

        err = ctx.exception
        declared = struct.unpack("!H", bytes(msg[2:4]))[0]
        # 报文中声明的值与按内容重算的值必须都带出来，且互不相同
        self.assertEqual(err.expected, declared)
        self.assertNotEqual(err.expected, err.actual)
        self.assertEqual(
            (err.expected, err.actual),
            (declared, internet_checksum(bytes(msg[:2]) + b"\x00\x00" + bytes(msg[4:]))),
        )
        self.assertIn("%04x" % declared, str(err))
        self.assertIn("%04x" % err.actual, str(err))

    def test_checksum_field_tampered(self):
        msg = bytearray(build_error_message(8, 0, b"ping"))
        msg[2] ^= 0xFF  # 直接篡改校验和字段本身
        with self.assertRaises(ChecksumMismatchError) as ctx:
            parse_error_message(bytes(msg))
        self.assertEqual(ctx.exception.expected, struct.unpack("!H", bytes(msg[2:4]))[0])
        self.assertNotEqual(ctx.exception.expected, ctx.exception.actual)


class LengthMismatch(unittest.TestCase):
    def test_truncated_message(self):
        msg = build_error_message(3, 0, b"\xaa\xbb\xcc")
        with self.assertRaises(LengthMismatchError) as ctx:
            parse_error_message(msg[:-1])  # 砍掉最后 1 字节
        self.assertEqual(ctx.exception.declared, len(msg))
        self.assertEqual(ctx.exception.actual, len(msg) - 1)

    def test_appended_byte(self):
        msg = build_error_message(3, 0, b"\xaa\xbb\xcc")
        with self.assertRaises(LengthMismatchError) as ctx:
            parse_error_message(msg + b"\x99")  # 多发 1 字节
        self.assertEqual(ctx.exception.declared, len(msg))
        self.assertEqual(ctx.exception.actual, len(msg) + 1)

    def test_forged_length_field(self):
        # 字节数没变但长度字段被伪造（重新填一个自洽的假校验和也救不了内容）
        msg = bytearray(build_error_message(3, 0, b"\x00"))
        forged = len(msg) + 100
        struct.pack_into("!H", msg, 4, forged)
        with self.assertRaises(LengthMismatchError) as ctx:
            parse_error_message(bytes(msg))
        self.assertEqual(ctx.exception.declared, forged)
        self.assertEqual(ctx.exception.actual, len(msg))

    def test_shorter_than_header(self):
        with self.assertRaises(LengthMismatchError):
            parse_error_message(b"\x03\x01\x00")


class FieldValidation(unittest.TestCase):
    def test_protocol_self_consistency_required(self):
        with self.assertRaises(ProtocolError):
            build_error_message(3, 0, b"", protocol=99)

    def test_field_range(self):
        with self.assertRaises(icmp_error.MessageError):
            build_error_message(256, 0, b"")
        with self.assertRaises(icmp_error.MessageError):
            build_error_message(3, -1, b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
