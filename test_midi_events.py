from __future__ import annotations

import unittest
from struct import pack

from midi_events import (
    ParseError,
    decode_vlq,
    encode_vlq,
    merge_tracks,
    parse_midi_file,
    parse_track,
    parse_tracks,
    serialize_track,
)


class VlqTests(unittest.TestCase):
    def test_boundaries_and_very_long_value(self) -> None:
        for value in (0, 1, 0x7F, 0x80, 0x3FFF, 0x4000, 0x0FFFFFFF, 0x100000000):
            encoded = encode_vlq(value)
            decoded, _, _ = decode_vlq(encoded)
            self.assertEqual(decoded, value)

        decoded, raw, _ = decode_vlq(b"\x81\x80\x80\x80\x00")
        self.assertEqual(decoded, 0x10000000)
        self.assertEqual(raw, b"\x81\x80\x80\x80\x00")

        with self.assertRaises(ParseError) as context:
            decode_vlq(b"\x80\x80")
        self.assertEqual(context.exception.reason, "unterminated-vlq")


class TrackParseTests(unittest.TestCase):
    def test_running_status_sysex_unknown_meta_and_round_trip(self) -> None:
        track = bytearray()
        track += b"\x00\x90\x3c\x64"
        track += b"\x00\x3d\x65"
        track += b"\x00\xff\x7e\x03\x01\x02\x03"
        track += b"\x00\xf0\x04\x7e\x01\x02\xf7"
        track += encode_vlq(0x100000000) + b"\x80\x3c\x00"
        track += b"\x00\xf4"
        original = bytes(track)

        events = parse_track(original)
        self.assertEqual([event.absolute_time for event in events], [0, 0, 0, 0, 0x100000000, 0x100000000])
        self.assertEqual(events[0].status, 0x90)
        self.assertEqual(events[1].status, 0x90)
        self.assertEqual(events[1].raw, b"\x3d\x65")
        self.assertEqual(events[2].kind, "meta")
        self.assertEqual(events[2].meta_type, 0x7E)
        self.assertEqual(events[2].data, (1, 2, 3))
        self.assertEqual(events[3].kind, "sysex")
        self.assertEqual(events[4].delta_time, 0x100000000)
        self.assertEqual(events[5].kind, "unknown-system")
        self.assertEqual(events[5].raw, b"\xf4")
        self.assertEqual(serialize_track(events), original)

    def test_nonminimal_delta_is_preserved(self) -> None:
        events = parse_track(b"\x80\x00\x90\x3c\x64")
        self.assertEqual(events[0].delta_time, 0)
        self.assertEqual(events[0].delta_raw, b"\x80\x00")
        self.assertEqual(serialize_track(events), b"\x80\x00\x90\x3c\x64")

    def test_empty_track(self) -> None:
        self.assertEqual(parse_track(b""), ())
        self.assertEqual(merge_tracks(parse_tracks([b"", b""])), [])

    def test_meta_event_clears_running_status(self) -> None:
        with self.assertRaises(ParseError) as context:
            parse_track(b"\x00\x90\x3c\x64\x00\xff\x01\x00\x00\x3d")
        self.assertEqual(context.exception.reason, "missing-running-status")


class MergeTests(unittest.TestCase):
    def test_absolute_time_cross_track_and_deterministic_ties(self) -> None:
        track0 = (
            b"\x00\x90\x3c\x64"
            + encode_vlq(5) + b"\x80\x3c\x00"
            + b"\x00\xff\x2f\x00"
        )
        track1 = (
            b"\x00\xc0\x01"
            + encode_vlq(5) + b"\x90\x40\x64"
        )

        merged = merge_tracks(parse_tracks([track0, track1]))
        self.assertEqual(
            [(event.track, event.sequence) for event in merged],
            [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)],
        )
        self.assertEqual([event.absolute_time for event in merged], [0, 0, 5, 5, 5])
        self.assertTrue(merged[-1].kind == "meta" and merged[-1].meta_type == 0x2F)


class TruncationTests(unittest.TestCase):
    def assert_reason(self, data: bytes, reason: str) -> None:
        with self.assertRaises(ParseError) as context:
            parse_track(data)
        self.assertEqual(context.exception.reason, reason)

    def test_truncated_track_forms(self) -> None:
        self.assert_reason(b"\x80", "unterminated-vlq-delta")
        self.assert_reason(b"\x00", "eof-status")
        self.assert_reason(b"\x00\x90", "eof-channel-data")
        self.assert_reason(b"\x00\x3c", "missing-running-status")
        self.assert_reason(b"\x00\xff", "eof-meta-type")
        self.assert_reason(b"\x00\xff\x7e\x02\x01", "eof-meta-data")
        self.assert_reason(b"\x00\xf0\x03\x01", "eof-sysex-data")
        self.assert_reason(b"\x00\xf1", "eof-system-data")

    def test_truncation_at_cross_track_boundary(self) -> None:
        with self.assertRaises(ParseError) as context:
            parse_tracks([b"\x00\x90\x3c\x64", b"\x80"])
        self.assertEqual(context.exception.reason, "unterminated-vlq-delta")
        self.assertEqual(context.exception.track, 1)


class MidiFileTests(unittest.TestCase):
    def test_file_with_tracks_and_unknown_chunk(self) -> None:
        track0 = b"\x00\xff\x2f\x00"
        track1 = b"\x00\x90\x3c\x64\x00\x80\x3c\x00\x00\xff\x2f\x00"
        data = (
            b"MThd"
            + pack(">I", 6)
            + pack(">HHH", 1, 2, 480)
            + b"XYZZ"
            + pack(">I", 2)
            + b"ab"
            + b"MTrk"
            + pack(">I", len(track0))
            + track0
            + b"MTrk"
            + pack(">I", len(track1))
            + track1
        )

        parsed = parse_midi_file(data)
        self.assertEqual(parsed.header.format_type, 1)
        self.assertEqual(parsed.header.division, 480)
        self.assertEqual(len(parsed.tracks), 2)
        self.assertEqual(parsed.unknown_chunks[0].chunk_type, b"XYZZ")
        self.assertEqual(parsed.unknown_chunks[0].data, b"ab")
        self.assertEqual(len(parsed.merged_events()), 4)

    def test_track_chunk_declares_more_data_than_available(self) -> None:
        data = (
            b"MThd"
            + pack(">I", 6)
            + pack(">HHH", 0, 1, 480)
            + b"MTrk"
            + pack(">I", 10)
            + b"\x00\xff\x2f\x00"
        )
        with self.assertRaises(ParseError) as context:
            parse_midi_file(data)
        self.assertEqual(context.exception.reason, "eof-chunk-data")


if __name__ == "__main__":
    unittest.main()
