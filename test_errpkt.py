#!/usr/bin/env python3
"""边界用例自测（unittest，仅标准库）。

运行: python3 -m unittest test_errpkt -v
"""

import unittest

from errpkt import (ChecksumMismatchError, LengthMismatchError,
                    ProtocolMismatchError, TruncatedPacketError,
                    build_packet, ones_complement_checksum, parse_packet)


class TestChecksum(unittest.TestCase):
    def test_odd_length_padding(self):
        # 01 02 03 -> 0x0102 + 0x0300 = 0x0402 -> 取反 0xFBFD
        self.assertEqual(ones_complement_checksum(b"\x01\x02\x03"), 0xFBFD)

    def test_all_zero_payload(self):
        self.assertEqual(ones_complement_checksum(b"\x00" * 10), 0xFFFF)

    def test_empty(self):
        self.assertEqual(ones_complement_checksum(b""), 0xFFFF)

    def test_carry_wraparound(self):
        # 0xFFFF + 0xFFFF 回卷后为 0xFFFF（即 -0），取反得 0x0000
        self.assertEqual(ones_complement_checksum(b"\xff\xff\xff\xff"),
                         0x0000)


class TestRoundTrip(unittest.TestCase):
    def test_odd_length_payload(self):
        packet = build_packet(11, 0, b"\x01\x02\x03")
        self.assertEqual(len(packet), 11)
        pkt = parse_packet(packet)
        self.assertEqual((pkt.type, pkt.code, pkt.payload),
                         (11, 0, b"\x01\x02\x03"))
        self.assertEqual(pkt.length, len(packet))

    def test_all_zero_payload(self):
        packet = build_packet(0, 0, b"\x00" * 8)
        pkt = parse_packet(packet)
        self.assertEqual(pkt.payload, b"\x00" * 8)

    def test_empty_payload(self):
        pkt = parse_packet(build_packet(3, 3))
        self.assertEqual(pkt.payload, b"")

    def test_max_payload(self):
        payload = bytes(range(256)) * 255 + bytes(range(247))
        packet = build_packet(5, 1, payload)
        pkt = parse_packet(packet)
        self.assertEqual(pkt.payload, payload)
        self.assertEqual(pkt.length, 0xFFFF)

    def test_invalid_fields_rejected(self):
        with self.assertRaises(ValueError):
            build_packet(256, 0)
        with self.assertRaises(ValueError):
            build_packet(0, -1)
        with self.assertRaises(ValueError):
            build_packet(0, 0, b"\x00" * 0xFFF8)


class TestTampering(unittest.TestCase):
    def test_tampered_checksum_reports_expected_and_actual(self):
        packet = build_packet(3, 1, b"payload")
        tampered = packet[:2] + b"\x12\x34" + packet[4:]
        with self.assertRaises(ChecksumMismatchError) as ctx:
            parse_packet(tampered)
        exc = ctx.exception
        self.assertEqual(exc.actual, 0x1234)
        self.assertEqual(exc.expected, pkt_checksum_of(packet))
        self.assertIn("0x1234", str(exc))
        self.assertIn(f"0x{exc.expected:04X}", str(exc))

    def test_tampered_payload_detected(self):
        packet = build_packet(3, 1, b"\xde\xad\xbe\xef")
        tampered = bytearray(packet)
        tampered[-1] ^= 0xFF
        with self.assertRaises(ChecksumMismatchError):
            parse_packet(bytes(tampered))

    def test_length_field_smaller_than_actual(self):
        packet = bytearray(build_packet(3, 1, b"payload"))
        packet[4:6] = (8).to_bytes(2, "big")  # 声明 8，实际 15
        with self.assertRaises(LengthMismatchError) as ctx:
            parse_packet(bytes(packet))
        self.assertEqual((ctx.exception.declared, ctx.exception.actual),
                         (8, 15))

    def test_length_field_larger_than_actual(self):
        packet = bytearray(build_packet(3, 1, b"payload"))
        packet[4:6] = (100).to_bytes(2, "big")
        with self.assertRaises(LengthMismatchError):
            parse_packet(bytes(packet))

    def test_truncated_packet(self):
        with self.assertRaises(TruncatedPacketError):
            parse_packet(b"\x03\x01\x00")
        packet = bytearray(build_packet(3, 1))
        packet[4:6] = (4).to_bytes(2, "big")  # 声明长度 < 首部长
        with self.assertRaises(TruncatedPacketError):
            parse_packet(bytes(packet))

    def test_protocol_mismatch(self):
        packet = bytearray(build_packet(3, 1, b"x", protocol=6))
        with self.assertRaises(ProtocolMismatchError) as ctx:
            parse_packet(bytes(packet), expected_protocol=1)
        self.assertEqual((ctx.exception.expected, ctx.exception.actual),
                         (1, 6))


def pkt_checksum_of(packet):
    return int.from_bytes(packet[2:4], "big")


if __name__ == "__main__":
    unittest.main()
