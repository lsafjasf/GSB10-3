#!/usr/bin/env python3
"""dns_axfr 边界用例自测（标准库 unittest）。

运行：python3 -m unittest test_dns_axfr -v
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_samples as bs  # noqa: E402
import dns_axfr as dns  # noqa: E402


def body(tcp_stream: bytes) -> bytes:
    """去掉 TCP 长度前缀，取第一个报文体。"""
    (mlen,) = struct.unpack_from("!H", tcp_stream, 0)
    return tcp_stream[2:2 + mlen]


class TestSingleRecord(unittest.TestCase):
    def test_single_a_record(self):
        msg = dns.parse_message(body(bs.build_single()))
        self.assertEqual(msg.header.msg_id, 0x0001)
        self.assertEqual(msg.header.qr, 1)
        self.assertTrue(msg.header.aa)
        self.assertEqual(len(msg.questions), 1)
        self.assertEqual(msg.questions[0].qname, "example.com.")
        self.assertEqual(msg.questions[0].qtype, "AXFR")
        self.assertEqual(len(msg.answers), 1)
        rec = msg.answers[0]
        self.assertEqual((rec.name, rec.type, rec.rclass, rec.ttl),
                         ("example.com.", "A", "IN", 300))
        self.assertEqual(rec.rdata, "192.0.2.1")


class TestCompression(unittest.TestCase):
    def test_heavily_compressed_names_and_rdata(self):
        msg = dns.parse_message(body(bs.build_compressed()))
        records = msg.all_records()
        self.assertEqual(len(records), 8)
        names = [r.name for r in records]
        self.assertEqual(names.count("example.com."), 4)
        self.assertIn("www.example.com.", names)
        self.assertIn("mail.example.com.", names)
        # RDATA 内的压缩指针
        mx = next(r for r in records if r.type == "MX")
        self.assertEqual(mx.rdata, "10 example.com.")
        cname = next(r for r in records if r.type == "CNAME")
        self.assertEqual(cname.rdata, "www.example.com.")
        ns = next(r for r in records if r.type == "NS")
        self.assertEqual(ns.rdata, "ns1.example.com.")

    def test_deep_pointer_chain(self):
        """指针 -> 指针 -> 指针 -> 真实标签，4 跳以上。"""
        msg = dns.parse_message(body(bs.build_deep_chain()))
        names = [r.name for r in msg.all_records()]
        self.assertIn("a.b.c.example.com.", names)
        self.assertIn("b.c.example.com.", names)
        self.assertIn("c.example.com.", names)

    def test_pointer_to_pointer_self_loop(self):
        """指针指向自身：单点环。"""
        data = bytearray(12)
        struct.pack_into("!HHHHHH", data, 0, 1, 0x8000, 0, 1, 0, 0)
        data.extend(struct.pack("!H", 0xC000 | 12))  # 名称 = 指向自己的指针
        data.extend(struct.pack("!HHIH", 1, 1, 0, 4))
        data.extend(b"\x01\x02\x03\x04")
        with self.assertRaises(dns.NameLoopError):
            dns.parse_message(bytes(data))

    def test_pointer_loop_between_two_offsets(self):
        with self.assertRaises(dns.NameLoopError) as ctx:
            dns.parse_message(body(bs.build_loop()))
        self.assertGreaterEqual(len(ctx.exception.chain), 2)

    def test_pointer_out_of_bounds_is_truncation(self):
        data = bytearray(12)
        struct.pack_into("!HHHHHH", data, 0, 1, 0x8000, 0, 1, 0, 0)
        data.extend(struct.pack("!H", 0xC000 | 0x0100))  # 指向报文外
        data.extend(struct.pack("!HHIH", 1, 1, 0, 4))
        data.extend(b"\x01\x02\x03\x04")
        with self.assertRaises(dns.TruncatedError) as ctx:
            dns.parse_message(bytes(data))
        self.assertGreater(ctx.exception.missing_bytes, 0)


class TestGrouping(unittest.TestCase):
    def test_grouping_preserves_original_order(self):
        msg = dns.parse_message(body(bs.build_compressed()))
        groups = dns.group_records(msg.all_records())
        a_group = groups[("example.com.", "A")]
        self.assertEqual([r.rdata for r in a_group], ["1.2.3.4", "5.6.7.8"])

    def test_grouping_across_stream_messages(self):
        result = dns.parse_stream(bs.build_stream())
        self.assertEqual(len(result.messages), 2)
        self.assertIsNone(result.error)
        groups = dns.group_records(result.records)
        # 两条 SOA（首 + 尾）保持原始顺序
        soa = groups[("zone.example.", "SOA")]
        self.assertEqual(len(soa), 2)
        self.assertEqual(soa[0].section, "answer")
        host = groups[("host.zone.example.", "A")]
        self.assertEqual([r.rdata for r in host],
                         ["192.168.1.10", "192.168.1.11"])


class TestTruncation(unittest.TestCase):
    def test_truncated_rdata_reports_count_and_missing(self):
        result = dns.parse_stream(bs.build_truncated_rr())
        self.assertIsNotNone(result.error)
        # 前两条 RR 完整，第三条 rdlength=100 只有 3 字节
        self.assertEqual(result.error.missing_bytes, 97)
        self.assertEqual(len(result.error.records), 2)
        self.assertEqual(result.to_dict()["error"]["parsed_records"], 2)

    def test_length_prefix_exceeds_content(self):
        result = dns.parse_stream(bs.build_truncated_len())
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.missing_bytes, 30)
        self.assertEqual(len(result.records), 0)

    def test_truncated_length_prefix_itself(self):
        result = dns.parse_stream(b"\x00")  # 长度前缀只有 1 字节
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.missing_bytes, 1)

    def test_truncated_header(self):
        with self.assertRaises(dns.TruncatedError) as ctx:
            dns.parse_message(b"\x00" * 5)
        self.assertEqual(ctx.exception.missing_bytes, 7)
        self.assertEqual(len(ctx.exception.records), 0)

    def test_truncated_mid_name(self):
        data = bytearray(12)
        struct.pack_into("!HHHHHH", data, 0, 1, 0x8000, 0, 1, 0, 0)
        data.extend(b"\x05ex")  # 标签声明 5 字节，只有 2 字节
        with self.assertRaises(dns.TruncatedError) as ctx:
            dns.parse_message(bytes(data))
        self.assertEqual(ctx.exception.missing_bytes, 3)

    def test_valid_prefix_messages_kept_on_later_truncation(self):
        """流中前面的报文完整、尾部截断时，已解析报文全部保留。"""
        stream = bs.build_single() + bs.build_truncated_len()
        result = dns.parse_stream(stream)
        self.assertEqual(len(result.messages), 1)
        self.assertEqual(len(result.records), 1)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.missing_bytes, 30)


class TestRdataTypes(unittest.TestCase):
    def test_soa_mx_aaaa_txt(self):
        b = bs.MessageBuilder()
        root = len(b.buf)
        b.add_rr(bs.enc_name("example.com"), 6,
                 bs.enc_name("ns.example.com") + bs.enc_name("hm.example.com")
                 + struct.pack("!IIIII", 1, 2, 3, 4, 5))
        b.add_rr(bs.ptr(root), 15, struct.pack("!H", 5) + bs.ptr(root))
        b.add_rr(bs.ptr(root), 28, bytes(range(16)))
        b.add_rr(bs.ptr(root), 16, bytes([5]) + b"hello")
        msg = dns.parse_message(b.build())
        soa, mx, aaaa, txt = msg.all_records()
        self.assertEqual(soa.rdata, "ns.example.com. hm.example.com. 1 2 3 4 5")
        self.assertEqual(mx.rdata, "5 example.com.")
        self.assertEqual(txt.rdata, '"hello"')
        import ipaddress
        self.assertEqual(aaaa.rdata,
                         str(ipaddress.IPv6Address(bytes(range(16)))))

    def test_unknown_type_falls_back_to_hex(self):
        b = bs.MessageBuilder()
        b.add_rr(bs.enc_name("example.com"), 65280, b"\xde\xad\xbe\xef")
        msg = dns.parse_message(b.build())
        rec = msg.all_records()[0]
        self.assertEqual(rec.type, "TYPE65280")
        self.assertEqual(rec.rdata, "deadbeef")


class TestSampleFiles(unittest.TestCase):
    """直接解析 samples/ 下的样本文件，保证构造脚本与库一致。"""

    SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "samples")

    def _parse(self, name):
        with open(os.path.join(self.SAMPLE_DIR, name), "rb") as f:
            return dns.parse_stream(f.read())

    def test_single_bin(self):
        result = self._parse("single.bin")
        self.assertIsNone(result.error)
        self.assertEqual(len(result.records), 1)

    def test_compressed_bin(self):
        result = self._parse("compressed.bin")
        self.assertIsNone(result.error)
        self.assertEqual(len(result.records), 8)

    def test_deep_chain_bin(self):
        result = self._parse("deep_chain.bin")
        self.assertIsNone(result.error)
        names = [r.name for r in result.records]
        self.assertIn("a.b.c.example.com.", names)

    def test_loop_bin(self):
        with open(os.path.join(self.SAMPLE_DIR, "loop.bin"), "rb") as f:
            data = f.read()
        with self.assertRaises(dns.NameLoopError):
            dns.parse_stream(data)

    def test_truncated_bins(self):
        self.assertEqual(self._parse("truncated_rr.bin").error.missing_bytes, 97)
        self.assertEqual(self._parse("truncated_len.bin").error.missing_bytes, 30)


if __name__ == "__main__":
    unittest.main()
