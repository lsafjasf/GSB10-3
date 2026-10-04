"""recordlayer 自测：覆盖题目要求的四类情形及更多边界。

直接运行::

    python3 tests/test_recordlayer.py          # unittest
    python3 -m unittest tests.test_recordlayer # 从仓库根目录

需要 captures/ 已由 tests/generate_captures.py 生成；
setUpClass 会在缺失时自动重新生成。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from recordlayer import (  # noqa: E402
    RecordAssembler,
    iter_events,
    parse_records,
    parse_handshake_messages,
    HandshakeMessage,
    Alert,
    ChangeCipherSpec,
    ApplicationData,
    UnknownRecord,
    MalformedRecordError,
    TruncatedRecordError,
    TruncatedHandshakeError,
    UnknownRecordTypeError,
    InterleavedRecordError,
    MAX_RECORD_LENGTH,
)
from tests.generate_captures import (  # noqa: E402
    record,
    handshake,
    build_handshake_stream,
    build_mixed_stream,
    build_truncated_samples,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(os.path.dirname(HERE), "captures")


def load_capture(name):
    with open(os.path.join(CAPTURE_DIR, name), "rb") as f:
        return f.read()


class SingleRecordTests(unittest.TestCase):
    """情形一：单条记录。"""

    def test_single_application_data(self):
        blob = record(23, b"hello")
        events = iter_events(blob)
        self.assertEqual(events, [ApplicationData(b"hello")])

    def test_single_record_fields(self):
        blob = record(23, b"hello", version=(3, 2))
        records = parse_records(blob)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].content_type, 23)
        self.assertEqual(records[0].version, (3, 2))
        self.assertEqual(records[0].body, b"hello")

    def test_single_zero_length_handshake(self):
        blob = record(22, handshake(16, b"", 0))
        messages = parse_handshake_messages(blob)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body, b"")
        self.assertEqual(messages[0].raw, handshake(16, b"", 0))

    def test_two_records_one_shot(self):
        blob = record(23, b"a") + record(23, b"bb")
        self.assertEqual(
            iter_events(blob), [ApplicationData(b"a"), ApplicationData(b"bb")]
        )


class ReassemblyTests(unittest.TestCase):
    """情形二：握手消息跨多条记录重组，并与一次性读取对拍。"""

    @classmethod
    def setUpClass(cls):
        cls.blob, cls.expected = build_handshake_stream()

    def test_one_shot_baseline(self):
        messages = parse_handshake_messages(self.blob)
        self.assertEqual(
            [(m.msg_type, m.message_seq, len(m.body)) for m in messages],
            [(e["msg_type"], e["message_seq"], e["body_length"]) for e in self.expected],
        )

    def _reassemble_chunked(self, chunk_size):
        assembler = RecordAssembler()
        collected = []
        for i in range(0, len(self.blob), chunk_size):
            collected.extend(assembler.feed(self.blob[i : i + chunk_size]))
        self.assertEqual(assembler.finish(), [])
        return collected

    def test_streaming_equals_one_shot_all_chunk_sizes(self):
        """块大小从 1 到全长逐一对拍：任意切点结果必须与一次性读取一致。"""
        baseline = parse_handshake_messages(self.blob)
        for chunk_size in range(1, len(self.blob) + 1):
            streamed = self._reassemble_chunked(chunk_size)
            self.assertEqual(streamed, baseline, "块大小 %d 时不一致" % chunk_size)

    def test_reassembly_preserves_raw_and_seq(self):
        messages = self._reassemble_chunked(7)
        for msg in messages:
            self.assertEqual(
                msg.raw,
                bytes([msg.msg_type])
                + len(msg.body).to_bytes(3, "big")
                + msg.message_seq.to_bytes(2, "big")
                + msg.body,
            )
            self.assertIsInstance(msg, HandshakeMessage)

    def test_multiple_handshakes_in_one_record(self):
        """一条记录里塞两条完整握手消息，也必须能拆出来。"""
        two = handshake(1, b"ab", 0) + handshake(2, b"cd", 1)
        blob = record(22, two)
        messages = parse_handshake_messages(blob)
        self.assertEqual([(m.msg_type, m.body) for m in messages], [(1, b"ab"), (2, b"cd")])

    def test_interleaved_record_during_handshake_is_error(self):
        """握手重组到一半插入告警记录：按状态机应拒绝而不是静默混在一起。"""
        full = handshake(1, b"x" * 50, 0)
        blob = (
            record(22, full[:30])
            + record(21, bytes([2, 40]))
            + record(22, full[30:])
        )
        with self.assertRaises(InterleavedRecordError):
            iter_events(blob)

        assembler = RecordAssembler()
        assembler.feed(record(22, full[:30]))
        with self.assertRaises(InterleavedRecordError):
            assembler.feed(record(21, bytes([2, 40])))


class TruncationTests(unittest.TestCase):
    """情形三：长度声明超过缓冲区时精确报告还缺多少字节，绝不截断。"""

    def test_incomplete_header_reports_missing(self):
        with self.assertRaises(TruncatedRecordError) as ctx:
            iter_events(record(23, b"")[:3])
        self.assertEqual(ctx.exception.missing, 2)

    def test_declared_length_exceeds_buffer(self):
        full = record(22, handshake(2, b"x" * 100, 7))
        partial = full[:-40]
        with self.assertRaises(TruncatedRecordError) as ctx:
            iter_events(partial)
        self.assertEqual(ctx.exception.missing, 40)
        self.assertEqual(ctx.exception.needed_total - ctx.exception.available, 40)

    def test_streaming_then_completion_never_truncates(self):
        """先给半截，异常对象给出缺口；补齐后正常解析，数据不能被截断。"""
        full = record(23, b"payload-data")
        assembler = RecordAssembler()
        self.assertEqual(assembler.feed(full[:8]), [])
        self.assertEqual(assembler.pending_record_bytes, 8)
        with self.assertRaises(TruncatedRecordError) as ctx:
            assembler.finish()
        self.assertEqual(ctx.exception.missing, len(full) - 8)
        events = assembler.feed(full[8:])
        self.assertEqual(events, [ApplicationData(b"payload-data")])
        assembler.finish()

    def test_truncated_handshake_body_reports_missing(self):
        """记录完整但握手消息体跨记录时缺尾：报缺多少握手字节。"""
        full = handshake(2, b"y" * 30, 5)
        blob = record(22, full[:-12])
        with self.assertRaises(TruncatedHandshakeError) as ctx:
            iter_events(blob)
        self.assertEqual(ctx.exception.missing, 12)

    def test_capture_samples_manifest(self):
        for sample in build_truncated_samples():
            path = os.path.join(CAPTURE_DIR, sample["filename"])
            with open(path, "rb") as f:
                data = f.read()
            with self.assertRaises(TruncatedRecordError) as ctx:
                iter_events(data)
            self.assertEqual(
                ctx.exception.missing, sample["missing"], sample["filename"]
            )

    def test_length_above_protocol_limit(self):
        blob = bytes([23, 3, 3, 0x40, 0x01]) + b""  # 声明 16385 > 16384
        with self.assertRaises(MalformedRecordError):
            iter_events(blob)
        self.assertEqual(MAX_RECORD_LENGTH, 0x4000)


class UnknownTypeAndDispatchTests(unittest.TestCase):
    """情形四：未知记录类型，以及按类型分发的总体行为。"""

    def test_unknown_type_is_kept_not_dropped(self):
        blob = record(0x55, b"\xde\xad\xbe\xef")
        events = iter_events(blob)
        self.assertEqual(events, [UnknownRecord(0x55, (3, 3), b"\xde\xad\xbe\xef")])

    def test_unknown_type_strict_mode_raises(self):
        blob = record(0x55, b"x")
        with self.assertRaises(UnknownRecordTypeError) as ctx:
            iter_events(blob, strict_unknown=True)
        self.assertEqual(ctx.exception.content_type, 0x55)
        with self.assertRaises(UnknownRecordTypeError):
            RecordAssembler(strict_unknown=True).feed(blob)

    def test_alert_kept_separate_with_raw_codes(self):
        blob = record(21, bytes([2, 40])) + record(23, b"data")
        events = iter_events(blob)
        alert, appdata = events
        self.assertIsInstance(alert, Alert)
        self.assertEqual((alert.level, alert.description), (2, 40))
        self.assertEqual(alert.raw, bytes([2, 40]))
        self.assertEqual(alert.level_name, "fatal")
        self.assertIsInstance(appdata, ApplicationData)
        self.assertNotIsInstance(appdata, Alert)

    def test_unknown_alert_codes_are_preserved(self):
        blob = record(21, bytes([9, 255]))  # 规范外码值，原样保留
        alert = iter_events(blob)[0]
        self.assertEqual((alert.level, alert.description), (9, 255))
        self.assertEqual(alert.level_name, "unknown(9)")

    def test_alert_wrong_length(self):
        with self.assertRaises(MalformedRecordError):
            iter_events(record(21, b"\x02"))
        with self.assertRaises(MalformedRecordError):
            iter_events(record(21, b"\x02\x28\x00"))

    def test_ccs_must_be_one(self):
        self.assertIsInstance(iter_events(record(20, b"\x01"))[0], ChangeCipherSpec)
        with self.assertRaises(MalformedRecordError):
            iter_events(record(20, b"\x00"))

    def test_bad_version(self):
        with self.assertRaises(MalformedRecordError):
            iter_events(record(23, b"x", version=(3, 4)))
        with self.assertRaises(MalformedRecordError):
            iter_events(record(23, b"x", version=(2, 0)))

    def test_custom_supported_versions(self):
        events = iter_events(
            record(23, b"x", version=(3, 4)),
            supported_versions=frozenset({(3, 4)}),
        )
        self.assertEqual(events, [ApplicationData(b"x")])

    def test_mixed_capture_dispatch_order(self):
        blob, expected = build_mixed_stream()
        events = iter_events(blob)
        kinds = [type(e).__name__ for e in events]
        self.assertEqual(kinds, [e["kind"] for e in expected])
        # 两个告警彼此独立，且与应用数据分离
        alerts = [e for e in events if isinstance(e, Alert)]
        self.assertEqual([(a.level, a.description) for a in alerts], [(2, 40), (1, 90)])
        # 未知类型不影响后续记录解析
        self.assertIsInstance(events[-1], ApplicationData)

    def test_mixed_capture_one_byte_feed(self):
        """混合流逐字节喂入，事件顺序/内容仍与一次性读取一致。"""
        blob, _ = build_mixed_stream()
        assembler = RecordAssembler()
        streamed = []
        for byte in blob:
            streamed.extend(assembler.feed(bytes([byte])))
        assembler.finish()
        self.assertEqual(streamed, iter_events(blob))

    def test_empty_input(self):
        self.assertEqual(iter_events(b""), [])
        assembler = RecordAssembler()
        self.assertEqual(assembler.feed(b""), [])
        assembler.finish()

    def test_reset_clears_partial_state(self):
        assembler = RecordAssembler()
        assembler.feed(record(23, b"abc")[:3])
        self.assertEqual(assembler.pending_record_bytes, 3)
        assembler.reset()
        self.assertEqual(assembler.pending_record_bytes, 0)
        self.assertEqual(assembler.feed(record(20, b"\x01")), [ChangeCipherSpec(b"\x01")])
        assembler.finish()


if __name__ == "__main__":
    # 缺 capture 时自动生成，保证开箱即跑
    if not os.path.exists(os.path.join(CAPTURE_DIR, "mixed_dispatch.bin")):
        sys.path.insert(0, HERE)
        from tests import generate_captures

        generate_captures.main()
    unittest.main(verbosity=2)
