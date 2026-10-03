"""边界用例：空轨道、超长增量时间、跨轨道边界、事件流截断等。

运行：python3 test_edge_cases.py -v
"""

import unittest

from midi_stream import (Event, encode_vlq, merge_tracks, parse_smf,
                         parse_track, read_vlq)


def kinds(events):
    return [e.kind for e in events]


class VlqTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(read_vlq(b'\x00', 0), (0, 1))
        self.assertEqual(read_vlq(b'\x7f', 0), (0x7F, 1))
        self.assertEqual(read_vlq(b'\x81\x00', 0), (128, 2))
        self.assertEqual(read_vlq(b'\xc0\x00', 0), (8192, 2))

    def test_encode_roundtrip(self):
        for v in (0, 1, 127, 128, 0x3FFF, 0x0FFFFFFF, 1 << 35):
            data = encode_vlq(v)
            self.assertEqual(read_vlq(data, 0), (v, len(data)))

    def test_truncated(self):
        value, pos = read_vlq(b'\x81\x80', 0)
        self.assertIsNone(value)
        self.assertEqual(pos, 2)


class EmptyTests(unittest.TestCase):
    def test_empty_track(self):
        self.assertEqual(parse_track(b''), [])

    def test_empty_tracks_merge(self):
        self.assertEqual(merge_tracks([[], [], []]), [])

    def test_only_end_of_track(self):
        evs = parse_track(b'\x00\xff\x2f\x00')
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, 'meta')
        self.assertEqual(evs[0].meta_type, 0x2F)


class LongDeltaTests(unittest.TestCase):
    def test_5_byte_delta(self):
        # 81 80 80 80 80 00 -> 1<<35（超出标准 4 字节 VLQ 上限仍可解析）
        evs = parse_track(b'\x81\x80\x80\x80\x80\x00\x90\x3c\x40')
        self.assertEqual(evs[0].delta, 1 << 35)
        self.assertEqual(evs[0].abs_time, 1 << 35)

    def test_huge_delta_then_zero(self):
        data = (encode_vlq(1 << 40) + b'\xc0\x10'
                + encode_vlq(0) + b'\xc0\x11')
        evs = parse_track(data)
        self.assertEqual([e.abs_time for e in evs], [1 << 40, 1 << 40])


class RunningStatusTests(unittest.TestCase):
    def test_running_status_then_meta_clears(self):
        data = (b'\x00\x90\x3c\x40'   # Note On
                b'\x00\x3e\x41'        # 运行状态：仍是 0x90
                b'\x00\xff\x01\x00'    # 元事件，清除运行状态
                b'\x00\x3f')           # 孤立数据字节 -> unknown
        evs = parse_track(data)
        self.assertEqual(kinds(evs),
                         ['channel', 'channel', 'meta', 'unknown'])
        self.assertEqual(evs[1].status, 0x90)
        self.assertEqual(evs[1].data, b'\x3e\x41')
        self.assertIsNone(evs[3].status)
        self.assertEqual(evs[3].raw, b'\x3f')

    def test_program_change_running(self):
        evs = parse_track(b'\x00\xc0\x00\x00\x01\x00\x02')
        self.assertEqual(kinds(evs), ['channel'] * 3)
        self.assertEqual([e.data for e in evs],
                         [b'\x00', b'\x01', b'\x02'])

    def test_realtime_does_not_clear(self):
        # Note On, 实时时钟 F8, 运行状态仍生效
        evs = parse_track(b'\x00\x90\x3c\x40\x00\xf8\x00\x3e\x41')
        self.assertEqual(kinds(evs), ['channel', 'system', 'channel'])
        self.assertEqual(evs[2].status, 0x90)


class SysexMetaTests(unittest.TestCase):
    def test_sysex_and_escape(self):
        evs = parse_track(b'\x00\xf0\x03\x01\x02\x03'
                          b'\x00\xf7\x02\x7e\x7f')
        self.assertEqual(kinds(evs), ['sysex', 'sysex'])
        self.assertEqual(evs[0].status, 0xF0)
        self.assertEqual(evs[0].data, b'\x01\x02\x03')
        self.assertEqual(evs[1].status, 0xF7)
        self.assertEqual(evs[1].data, b'\x7e\x7f')

    def test_unknown_status_preserved(self):
        evs = parse_track(b'\x00\xf4\x00\xf5\x00\xfd')
        self.assertEqual(kinds(evs), ['unknown', 'unknown', 'unknown'])
        self.assertEqual([e.status for e in evs], [0xF4, 0xF5, 0xFD])
        self.assertEqual([e.raw for e in evs],
                         [b'\xf4', b'\xf5', b'\xfd'])

    def test_system_common(self):
        evs = parse_track(b'\x00\xf1\x01\x00\xf2\x01\x02'
                          b'\x00\xf3\x03\x00\xf6')
        self.assertEqual(kinds(evs), ['system'] * 4)
        self.assertEqual(evs[0].data, b'\x01')
        self.assertEqual(evs[1].data, b'\x01\x02')
        self.assertEqual(evs[3].data, b'')

    def test_meta_types_preserved(self):
        # 不认识的元事件类型（0x70）也原样保留
        evs = parse_track(b'\x00\xff\x70\x02\xaa\xbb')
        self.assertEqual(evs[0].kind, 'meta')
        self.assertEqual(evs[0].meta_type, 0x70)
        self.assertEqual(evs[0].data, b'\xaa\xbb')


class TruncationTests(unittest.TestCase):
    def _one_truncated(self, data, count=1):
        evs = parse_track(data)
        self.assertEqual(len(evs), count)
        self.assertEqual(evs[-1].kind, 'truncated')
        return evs

    def test_truncated_delta_vlq(self):
        evs = self._one_truncated(b'\x81\x80')
        self.assertIsNone(evs[0].delta)
        self.assertEqual(evs[0].raw, b'\x81\x80')

    def test_status_only(self):
        evs = self._one_truncated(b'\x00\x90')
        self.assertEqual(evs[0].delta, 0)
        self.assertEqual(evs[0].status, 0x90)
        self.assertEqual(evs[0].raw, b'\x90')

    def test_one_of_two_data_bytes(self):
        evs = self._one_truncated(b'\x00\x90\x3c')
        self.assertEqual(evs[0].data, b'\x3c')
        self.assertEqual(evs[0].raw, b'\x90\x3c')

    def test_running_status_truncated(self):
        evs = self._one_truncated(b'\x00\x90\x3c\x40\x00\x3e', 2)
        self.assertEqual(evs[0].kind, 'channel')
        self.assertEqual(evs[1].status, 0x90)
        self.assertEqual(evs[1].raw, b'\x3e')

    def test_sysex_truncated_length(self):
        evs = self._one_truncated(b'\x00\xf0\x81')
        self.assertEqual(evs[0].raw, b'\xf0\x81')

    def test_sysex_truncated_payload(self):
        evs = self._one_truncated(b'\x00\xf0\x05\x01\x02')
        self.assertEqual(evs[0].data, b'\x01\x02')
        self.assertEqual(evs[0].raw, b'\xf0\x05\x01\x02')

    def test_meta_type_missing(self):
        evs = self._one_truncated(b'\x00\xff')
        self.assertIsNone(evs[0].meta_type)

    def test_meta_length_truncated(self):
        evs = self._one_truncated(b'\x00\xff\x2f\x81')
        self.assertEqual(evs[0].meta_type, 0x2F)
        self.assertEqual(evs[0].raw, b'\xff\x2f\x81')

    def test_meta_payload_truncated(self):
        evs = self._one_truncated(b'\x00\xff\x2f\x02\x00')
        self.assertEqual(evs[0].meta_type, 0x2F)
        self.assertEqual(evs[0].data, b'\x00')

    def test_delta_without_event(self):
        evs = self._one_truncated(b'\x00')
        self.assertEqual(evs[0].delta, 0)
        self.assertEqual(evs[0].raw, b'')


class MergeOrderTests(unittest.TestCase):
    def test_cross_track_tie_break(self):
        t0 = parse_track(b'\x00\xff\x01\x01\x41'
                         b'\x0a\xff\x01\x01\x42')
        t1 = parse_track(b'\x00\x90\x3c\x40'
                         b'\x0a\x90\x3c\x00', track=1)
        merged = merge_tracks([t0, t1])
        # 同一时刻：轨道 0 先于轨道 1；同轨道内按原顺序
        self.assertEqual([(e.abs_time, e.track, e.order) for e in merged],
                         [(0, 0, 0), (0, 1, 0), (10, 0, 1), (10, 1, 1)])

    def test_deterministic(self):
        t0 = parse_track(b'\x00\x90\x3c\x40\x05\x90\x3c\x00')
        t1 = parse_track(b'\x00\x90\x3e\x40\x05\x90\x3e\x00', track=1)
        a = merge_tracks([t0, t1])
        b = merge_tracks([t0, t1])
        self.assertEqual([e.signature() for e in a],
                         [e.signature() for e in b])

    def test_absolute_time_accumulation(self):
        evs = parse_track(b'\x03\x90\x3c\x40\x02\x80\x3c\x00')
        self.assertEqual([e.abs_time for e in evs], [3, 5])


class SmfTests(unittest.TestCase):
    def _build(self, payloads, truncate=None, fmt=1, division=480):
        data = b'MThd' + (6).to_bytes(4, 'big')
        data += fmt.to_bytes(2, 'big') + len(payloads).to_bytes(2, 'big')
        data += division.to_bytes(2, 'big')
        data += b'Xyzw' + (4).to_bytes(4, 'big') + b'????'  # 未知 chunk
        for p in payloads:
            data += b'MTrk' + len(p).to_bytes(4, 'big') + p
        if truncate is not None:
            data = data[:truncate]
        return data

    def test_parse_smf(self):
        header, tracks = parse_smf(self._build(
            [b'\x00\xff\x2f\x00', b'\x00\x90\x3c\x40\x00\xff\x2f\x00']))
        self.assertEqual(header['format'], 1)
        self.assertEqual(header['ntrks'], 2)
        self.assertEqual(header['division'], 480)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0][0].track, 0)
        self.assertEqual(tracks[1][0].track, 1)

    def test_truncated_file(self):
        full = self._build([b'\x00\xff\x2f\x00', b'\x00\x90\x3c\x40'])
        # 在第二条 MTrk 负载中间截断，解析器不应崩溃
        header, tracks = parse_smf(full[:len(full) - 2])
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[1][-1].kind, 'truncated')

    def test_bad_header(self):
        with self.assertRaises(ValueError):
            parse_smf(b'XXXX' + b'\x00' * 20)


if __name__ == '__main__':
    unittest.main(verbosity=2)
