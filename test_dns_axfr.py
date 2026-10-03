"""dns_axfr 边界用例自测（标准库 unittest）。

覆盖：单条记录、大量压缩指针（含多级回跳）、指针成环、报文截断、
长度前缀不符、分组顺序保持。
"""

import struct
import unittest

import build_samples
from dns_axfr import (
    NameLoopError,
    TruncatedError,
    group_records,
    parse_message,
    parse_name,
    parse_stream,
)


class SingleRecordTest(unittest.TestCase):
    def test_header_question_record(self):
        message = parse_message(build_samples.build_single_message())
        self.assertEqual(message.header.id, 0x1160)
        self.assertEqual(message.header.qr, 1)
        self.assertEqual(message.header.ancount, 1)
        self.assertEqual(message.questions[0].name, "www.example.com")
        self.assertEqual(message.questions[0].qtype, 1)
        record = message.answers[0]
        self.assertEqual(record.name, "www.example.com")
        self.assertEqual(record.type_name, "A")
        self.assertEqual(record.ttl, 300)
        self.assertEqual(record.rdata, "93.184.216.34")
        self.assertEqual(record.section, "answer")


class CompressionPointerTest(unittest.TestCase):
    def setUp(self):
        self.message = parse_message(build_samples.build_pointers_message())

    def test_record_count(self):
        self.assertEqual(self.message.record_count, 9)

    def test_deep_backjump_names_resolved(self):
        names = [r.name for r in self.message.answers]
        self.assertEqual(
            names,
            ["example.com", "example.com", "example.com",
             "ns1.example.com", "ns2.example.com",
             "www.example.com", "www.example.com",
             "web.www.example.com", "example.com"],
        )

    def test_chained_pointer_in_rdata(self):
        # web.www.example.com 的名称与 CNAME 目标都经过二级指针回跳
        cname = self.message.answers[7]
        self.assertEqual(cname.type_name, "CNAME")
        self.assertEqual(cname.rdata, "www.example.com")

    def test_soa_rdata_with_compressed_names(self):
        soa = self.message.answers[0]
        self.assertEqual(
            soa.rdata,
            "ns1.example.com hostmaster.example.com "
            "2026100301 7200 3600 1209600 300",
        )

    def test_arbitrary_depth_jump(self):
        # 手工构造 5 级指针链：每一级都指向前一级
        data = bytearray(b"\x00" * 12)
        data += b"\x03www\x07example\x03com\x00"  # 12: 完整名
        offsets = [12]
        for _ in range(4):
            offsets.append(len(data))
            data += struct.pack("!H", 0xC000 | offsets[-2])
        name, end = parse_name(bytes(data), offsets[-1])
        self.assertEqual(name, "www.example.com")
        self.assertEqual(end, offsets[-1] + 2)


class PointerLoopTest(unittest.TestCase):
    def test_self_loop(self):
        with self.assertRaises(NameLoopError):
            parse_message(build_samples.build_loop_message())

    def test_mutual_loop(self):
        with self.assertRaises(NameLoopError):
            parse_message(build_samples.build_mutual_loop_message())

    def test_loop_offset_reported(self):
        try:
            parse_message(build_samples.build_loop_message())
        except NameLoopError as exc:
            self.assertEqual(exc.offset, 12)
        else:
            self.fail("expected NameLoopError")


class TruncationTest(unittest.TestCase):
    def test_frame_length_mismatch(self):
        # 完整第 1 帧（1 条记录）+ 第 2 帧长度前缀多报 20 字节
        with self.assertRaises(TruncatedError) as ctx:
            parse_stream(build_samples.build_truncated_stream(missing=20))
        self.assertEqual(ctx.exception.records_parsed, 1)
        self.assertEqual(ctx.exception.missing_bytes, 20)

    def test_record_rdata_truncated(self):
        # A 记录 RDATA 声明 4 字节只给 2 字节
        with self.assertRaises(TruncatedError) as ctx:
            parse_message(build_samples.build_truncated_record_message())
        self.assertEqual(ctx.exception.records_parsed, 0)
        self.assertEqual(ctx.exception.missing_bytes, 2)

    def test_header_truncated(self):
        with self.assertRaises(TruncatedError) as ctx:
            parse_message(b"\x00" * 5)
        self.assertEqual(ctx.exception.records_parsed, 0)
        self.assertEqual(ctx.exception.missing_bytes, 7)

    def test_mid_message_truncation_reports_parsed_count(self):
        # 多记录报文从中间截断：已解析的记录数应被保留
        full = build_samples.build_pointers_message()
        cut = full[: len(full) - 30]
        with self.assertRaises(TruncatedError) as ctx:
            parse_message(cut)
        self.assertGreater(ctx.exception.records_parsed, 0)
        self.assertLess(ctx.exception.records_parsed, 9)
        self.assertGreater(ctx.exception.missing_bytes, 0)

    def test_dangling_length_prefix(self):
        with self.assertRaises(TruncatedError) as ctx:
            parse_stream(build_samples.build_dangling_prefix_stream())
        self.assertEqual(ctx.exception.records_parsed, 1)
        self.assertEqual(ctx.exception.missing_bytes, 1)

    def test_name_truncated(self):
        with self.assertRaises(TruncatedError) as ctx:
            parse_name(b"\x03ww", 0)
        # 标签声明 3 字节内容只给 2 字节，缺 1 字节
        self.assertEqual(ctx.exception.missing_bytes, 1)


class StreamAndGroupingTest(unittest.TestCase):
    def test_stream_two_frames(self):
        messages = parse_stream(build_samples.build_stream())
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].record_count, 1)
        self.assertEqual(messages[1].record_count, 9)

    def test_grouping_by_name_and_type(self):
        messages = parse_stream(build_samples.build_stream())
        groups = group_records(messages)
        keys = [(g["name"], g["type"]) for g in groups]
        self.assertEqual(
            keys,
            [("www.example.com", "A"),
             ("example.com", "SOA"),
             ("example.com", "NS"),
             ("ns1.example.com", "A"),
             ("ns2.example.com", "A"),
             ("web.www.example.com", "CNAME")],
        )

    def test_same_name_records_keep_original_order(self):
        messages = parse_stream(build_samples.build_stream())
        groups = group_records(messages)
        www = next(g for g in groups if g["name"] == "www.example.com")
        self.assertEqual(
            [r["rdata"] for r in www["records"]],
            ["93.184.216.34", "192.0.2.80", "192.0.2.81"],
        )
        soa = next(g for g in groups if g["type"] == "SOA")
        self.assertEqual(len(soa["records"]), 2)
        ns = next(g for g in groups if g["type"] == "NS")
        self.assertEqual(
            [r["rdata"] for r in ns["records"]],
            ["ns1.example.com", "ns2.example.com"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
