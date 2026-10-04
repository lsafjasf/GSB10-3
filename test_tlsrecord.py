"""tlsrecord 自测: 记录解析 / 类型分发 / 跨记录重组 / 边界情形。

运行:
    python3 -m unittest -v
    python3 test_tlsrecord.py
"""

import json
import os
import random
import unittest

from tlsrecord import (
    Alert,
    AlertFormatError,
    AppData,
    ChangeCipherSpec,
    ContentType,
    HandshakeMessage,
    HandshakeReassembler,
    MAX_RECORD_LENGTH,
    NeedMoreDataError,
    Record,
    RecordDispatcher,
    RecordOverflowError,
    RecordParser,
    RecordLayerError,
    UnknownContentTypeError,
    UnsupportedVersionError,
    build_handshake,
    build_record,
    parse_alerts,
    parse_record,
    parse_records,
)

HERE = os.path.dirname(os.path.abspath(__file__))
VECTOR_PATH = os.path.join(HERE, "testdata", "reassembly_vectors.json")
VERSION = 0x0303


def reassemble_records(record_blobs):
    """解析记录流 -> 握手重组, 返回握手消息列表。"""
    reassembler = HandshakeReassembler()
    messages = []
    for blob in record_blobs:
        for record in parse_records(blob):
            messages.extend(reassembler.feed(record.fragment))
    return messages


class SingleRecordTests(unittest.TestCase):
    """单条记录解析。"""

    def test_handshake_record(self):
        raw = build_record(22, VERSION, b"\x01\x00\x00\x03abc")
        record, offset = parse_record(raw)
        self.assertEqual(record.content_type, ContentType.HANDSHAKE)
        self.assertEqual(record.version, VERSION)
        self.assertEqual(record.fragment, b"\x01\x00\x00\x03abc")
        self.assertEqual(offset, len(raw))

    def test_each_known_type_round_trips(self):
        for ctype in ContentType:
            fragment = b"\x01" if ctype == ContentType.CHANGE_CIPHER_SPEC else b"x"
            record, offset = parse_record(build_record(ctype, VERSION, fragment))
            self.assertEqual(record.content_type, ctype)

    def test_accepted_legacy_versions(self):
        for ver in (0x0300, 0x0301, 0x0302, 0x0303):
            record, _ = parse_record(build_record(23, ver, b""))
            self.assertEqual(record.version, ver)

    def test_multiple_records_one_shot(self):
        raw = (
            build_record(20, VERSION, b"\x01")
            + build_record(23, VERSION, b"hello")
        )
        records = parse_records(raw)
        self.assertEqual([r.content_type for r in records],
                         [ContentType.CHANGE_CIPHER_SPEC, ContentType.APPLICATION_DATA])
        self.assertEqual(records[1].fragment, b"hello")

    def test_zero_length_record(self):
        record, offset = parse_record(build_record(23, VERSION, b""))
        self.assertEqual(record.fragment, b"")
        self.assertEqual(parse_records(build_record(23, VERSION, b"")), [record])


class ReassemblyDifferentialTests(unittest.TestCase):
    """跨记录重组对拍: 多条记录的结果必须与一次性读取一致。"""

    @classmethod
    def setUpClass(cls):
        with open(VECTOR_PATH, encoding="utf-8") as fh:
            cls.vectors = json.load(fh)["cases"]

    def test_vectors_file_is_nonempty(self):
        self.assertGreaterEqual(len(self.vectors), 6)

    def test_fragmented_equals_oneshot_for_each_vector(self):
        for case in self.vectors:
            with self.subTest(case=case["name"]):
                expected = [bytes.fromhex(h) for h in case["messages_hex"]]
                oneshot_blobs = [bytes.fromhex(case["oneshot_record_hex"])]
                fragmented_blobs = [
                    bytes.fromhex(h) for h in case["fragmented_record_hex"]
                ]

                from_oneshot = reassemble_records(oneshot_blobs)
                from_fragmented = reassemble_records(fragmented_blobs)

                self.assertEqual(from_fragmented, from_oneshot)
                self.assertEqual([m.raw for m in from_fragmented], expected)
                # 重组器不应残留字节
                reassembler = HandshakeReassembler()
                for blob in fragmented_blobs:
                    for record in parse_records(blob):
                        reassembler.feed(record.fragment)
                self.assertEqual(reassembler.buffered, 0)

    def test_random_splits_always_match_one_shot(self):
        rng = random.Random(20261004)
        messages = [
            build_handshake(1, rng.randbytes(rng.randrange(0, 200)))
            for _ in range(8)
        ] + [build_handshake(20, b"")]
        stream = b"".join(messages)
        oneshot = reassemble_records([build_record(22, VERSION, stream)])

        for _ in range(50):
            cuts = sorted(rng.sample(range(1, len(stream)),
                                     rng.randrange(1, min(40, len(stream)))))
            fragments, prev = [], 0
            for cut in cuts + [len(stream)]:
                fragments.append(stream[prev:cut])
                prev = cut
            blobs = [build_record(22, VERSION, f) for f in fragments if f]
            self.assertEqual(reassemble_records(blobs), oneshot)

    def test_partial_handshake_stays_buffered(self):
        msg = build_handshake(1, b"abcdef")
        reassembler = HandshakeReassembler()
        self.assertEqual(reassembler.feed(msg[:2]), [])
        self.assertEqual(reassembler.buffered, 2)
        self.assertEqual(reassembler.feed(msg[2:5]), [])
        done = reassembler.feed(msg[5:])
        self.assertEqual(done, [HandshakeMessage(1, b"abcdef")])
        self.assertEqual(reassembler.buffered, 0)

    def test_coalesced_handshake_messages_in_one_record(self):
        blob = build_record(
            22, VERSION,
            build_handshake(1, b"hi") + build_handshake(20, b"bye"),
        )
        msgs = reassemble_records([blob])
        self.assertEqual(msgs, [HandshakeMessage(1, b"hi"),
                                HandshakeMessage(20, b"bye")])


class IncrementalParserTests(unittest.TestCase):
    """增量喂入与一次性解析结果一致; 缺口可量化。"""

    def test_byte_by_byte_feed_equals_bulk(self):
        stream = (
            build_record(22, VERSION, build_handshake(1, b"x" * 30))
            + build_record(21, VERSION, b"\x01\x00")
            + build_record(23, VERSION, b"payload")
        )
        bulk = parse_records(stream)

        parser = RecordParser()
        got = []
        for byte in stream:
            got.extend(parser.feed(bytes([byte])))
            if parser.buffered:
                # 缓冲区里还有未完成的记录时, needed 必须给出正缺口
                self.assertGreater(parser.needed, 0)
        self.assertEqual(got, bulk)
        self.assertEqual(parser.needed, 0)
        self.assertEqual(parser.buffered, 0)

    def test_needed_reports_exact_missing_bytes_for_header(self):
        parser = RecordParser()
        parser.feed(b"\x16\x03\x03")
        self.assertEqual(parser.buffered, 3)
        self.assertEqual(parser.needed, 2)

    def test_needed_reports_exact_missing_bytes_for_fragment(self):
        parser = RecordParser()
        # 声明 fragment 100 字节, 实际只到 10 字节
        parser.feed(bytes([23]) + VERSION.to_bytes(2, "big")
                    + (100).to_bytes(2, "big") + b"0123456789")
        self.assertEqual(parser.needed, 90)
        parser.feed(b"z" * 89)
        self.assertEqual(parser.needed, 1)
        records = parser.feed(b"z")
        self.assertEqual(len(records), 1)
        self.assertEqual(parser.needed, 0)


class LengthAndVersionValidationTests(unittest.TestCase):
    """长度/版本字段校验, 缺口报告而不是截断。"""

    def test_declared_length_exceeds_buffer_reports_gap(self):
        header = bytes([22]) + VERSION.to_bytes(2, "big") + (100).to_bytes(2, "big")
        partial = header + b"short"  # 只有 5 字节 fragment
        with self.assertRaises(NeedMoreDataError) as ctx:
            parse_record(partial)
        # 5(头) + 100 - 10(已有) = 95
        self.assertEqual(ctx.exception.missing, 95)

    def test_partial_header_reports_gap(self):
        with self.assertRaises(NeedMoreDataError) as ctx:
            parse_record(b"\x16\x03")
        self.assertEqual(ctx.exception.missing, 3)

    def test_trailing_partial_record_reports_gap(self):
        good = build_record(23, VERSION, b"ok")
        with self.assertRaises(NeedMoreDataError) as ctx:
            parse_records(good + b"\x16\x03\x03\x00\x05ab")
        self.assertEqual(ctx.exception.missing, 3)

    def test_declared_length_above_protocol_limit(self):
        raw = bytes([23]) + VERSION.to_bytes(2, "big") + (65535).to_bytes(2, "big")
        with self.assertRaises(RecordOverflowError) as ctx:
            parse_record(raw)
        self.assertEqual(ctx.exception.declared, 65535)
        self.assertEqual(ctx.exception.maximum, MAX_RECORD_LENGTH)

    def test_max_allowed_length_is_parseable(self):
        fragment = b"q" * MAX_RECORD_LENGTH
        record, offset = parse_record(build_record(23, VERSION, fragment))
        self.assertEqual(len(record.fragment), MAX_RECORD_LENGTH)
        self.assertEqual(offset, 5 + MAX_RECORD_LENGTH)

    def test_bad_version_rejected(self):
        with self.assertRaises(UnsupportedVersionError) as ctx:
            parse_record(build_record(23, 0x0304, b"x"))
        self.assertEqual(ctx.exception.version, 0x0304)
        with self.assertRaises(UnsupportedVersionError):
            parse_record(build_record(23, 0x0200, b"x"))


class UnknownTypeTests(unittest.TestCase):
    """未知记录类型。"""

    def test_unknown_content_type_raised_with_raw_byte(self):
        raw = bytes([99]) + VERSION.to_bytes(2, "big") + (0).to_bytes(2, "big")
        with self.assertRaises(UnknownContentTypeError) as ctx:
            parse_record(raw)
        self.assertEqual(ctx.exception.type_byte, 99)

    def test_unknown_type_amid_good_records_not_consumed(self):
        stream = build_record(23, VERSION, b"a") + bytes(
            [0x63]
        ) + VERSION.to_bytes(2, "big") + (0).to_bytes(2, "big")
        with self.assertRaises(UnknownContentTypeError):
            parse_records(stream)

    def test_unknown_type_is_record_layer_error(self):
        self.assertTrue(issubclass(UnknownContentTypeError, RecordLayerError))


class AlertTests(unittest.TestCase):
    """告警单独归类, 原始码值保留。"""

    def test_alert_raw_codes_preserved_for_unknown_values(self):
        alerts = parse_alerts(bytes([0xEE, 0x42]))
        self.assertEqual(alerts, [Alert(0xEE, 0x42)])
        self.assertEqual(alerts[0].level, 0xEE)
        self.assertEqual(alerts[0].description, 0x42)
        self.assertIn("unknown(238)", alerts[0].level_name)

    def test_known_alert_names(self):
        alert = parse_alerts(bytes([1, 0]))[0]
        self.assertEqual(alert.level_name, "warning")
        self.assertEqual(alert.description_name, "close_notify")

    def test_malformed_alert_fragment(self):
        with self.assertRaises(AlertFormatError):
            parse_alerts(b"\x02")
        with self.assertRaises(AlertFormatError):
            parse_alerts(b"")

    def test_multiple_alerts_in_one_record(self):
        alerts = parse_alerts(bytes([2, 40, 1, 0]))
        self.assertEqual(alerts, [Alert(2, 40), Alert(1, 0)])


class DispatchTests(unittest.TestCase):
    """按记录类型分发, 告警不与普通数据混淆。"""

    def test_dispatch_buckets_are_separated(self):
        stream = (
            build_record(22, VERSION, build_handshake(1, b"CH")[:6])  # 半截握手
            + build_record(21, VERSION, bytes([1, 90]))               # 告警插入
            + build_record(22, VERSION, build_handshake(1, b"CH")[6:])
            + build_record(23, VERSION, b"app-bytes")
            + build_record(20, VERSION, b"\x01")
        )
        dispatcher = RecordDispatcher()
        parser = RecordParser()
        events = dispatcher.feed_records(parser.feed(stream))

        # 握手消息跨越了一条中间夹杂告警的记录, 仍正确重组
        self.assertEqual(dispatcher.handshakes, [HandshakeMessage(1, b"CH")])
        self.assertEqual(dispatcher.alerts, [Alert(1, 90)])
        self.assertEqual(dispatcher.app_data, [AppData(b"app-bytes")])
        self.assertEqual(dispatcher.change_cipher_specs,
                         [ChangeCipherSpec(b"\x01")])

        # 告警绝不出现在应用数据/握手桶里
        self.assertNotIn(Alert(1, 90), dispatcher.handshakes)
        self.assertTrue(all(not isinstance(e, Alert)
                            for e in dispatcher.handshakes + dispatcher.app_data))
        self.assertEqual(len(events), 4)  # 握手1 + 告警1 + app1 + ccs1

    def test_dispatch_returns_events_in_order(self):
        stream = (
            build_record(21, VERSION, bytes([2, 40]))
            + build_record(23, VERSION, b"p")
        )
        dispatcher = RecordDispatcher()
        events = dispatcher.feed_records(parse_records(stream))
        self.assertEqual([type(e) for e in events], [Alert, AppData])


if __name__ == "__main__":
    unittest.main(verbosity=2)
