"""mvarchive 自测：python3 -m unittest -v test_mvarchive

覆盖：单卷、乱序命名、中间卷缺失（含定位）、卷内长度不一致、
重复卷号、总卷数不一致、跨卷分块读取对拍、seek、CRC 等边界情形。
"""

import os
import random
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import mvarchive as mva


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mva-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_raw(self, vol_no, vol_total, payload, name=None,
                   declared_len=None, crc=None, truncate=0):
        declared_len = len(payload) if declared_len is None else declared_len
        crc = zlib.crc32(payload) & 0xFFFFFFFF if crc is None else crc
        raw = mva.HEADER.pack(
            mva.MAGIC, mva.VERSION, 0, vol_no, vol_total, declared_len, crc
        ) + payload
        if truncate:
            raw = raw[:-truncate]
        p = self.tmp / (name or f"x{vol_no}.bin")
        p.write_bytes(raw)
        return p


class TestBasic(_Tmp):
    def test_single_volume(self):
        data = b"hello, single volume world!"
        mva.write_volumes(data, self.tmp, max_data_len=1 << 20)
        files = list(self.tmp.glob("*.bin"))
        self.assertEqual(len(files), 1)
        with mva.open_volume_stream(self.tmp) as r:
            self.assertEqual(r.total_volumes, 1)
            self.assertEqual(r.length, len(data))
            self.assertEqual(r.read(), data)

    def test_empty_data_is_one_volume(self):
        mva.write_volumes(b"", self.tmp, 16)
        with mva.open_volume_stream(self.tmp) as r:
            self.assertEqual(r.total_volumes, 1)
            self.assertEqual(r.read(), b"")
            self.assertEqual(r.read(), b"")  # 到流尾后继续读不报错

    def test_multi_volume_sorted_by_header_not_name(self):
        data = bytes(range(256)) * 4  # 1024 字节
        mva.write_volumes(data, self.tmp, max_data_len=100)  # 11 卷
        # 故意乱序 + 字典序错误命名
        targets = ["zz.bin", "aa.bin", "m.bin", "b.bin", "c.bin",
                   "d.bin", "e.bin", "f.bin", "g.bin", "h.bin", "i.bin"]
        for i, name in enumerate(targets):
            src = self.tmp / f"part.{i+1:05d}-of-00011.bin"
            src.rename(self.tmp / name)
        with mva.open_volume_stream(self.tmp) as r:
            self.assertEqual(r.total_volumes, 11)
            self.assertEqual([v.vol_no for v in r.volumes], list(range(1, 12)))
            self.assertEqual(r.read_all_concat(), data)
            self.assertEqual(r.read(), data)

    def test_explicit_file_list_ignores_dir_order(self):
        data = b"0123456789ABCDEF"
        paths = mva.write_volumes(data, self.tmp, 4)
        random.Random(42).shuffle(paths)
        with mva.open_volume_stream(paths) as r:
            self.assertEqual(r.read(), data)


class TestGapDetection(_Tmp):
    def _make_three(self):
        # 3 卷，每卷载荷 10 字节
        return [self._write_raw(i, 3, bytes([i]) * 10) for i in (1, 2, 3)]

    def test_missing_middle_volume(self):
        self._make_three()
        (self.tmp / "x2.bin").unlink()
        with self.assertRaises(mva.MissingVolumeError) as cm:
            mva.open_volume_stream(self.tmp)
        err = cm.exception
        self.assertEqual(err.vol_total, 3)
        self.assertEqual(len(err.gaps), 1)
        gap = err.gaps[0]
        self.assertEqual(gap.missing, [2])
        self.assertEqual((gap.after_vol, gap.before_vol), (1, 3))
        self.assertEqual(gap.byte_offset, 10)  # 缺口在第 1 卷 10 字节之后
        self.assertIn("卷 1 与卷 3", gap.describe())

    def test_missing_head_and_tail(self):
        self._make_three()
        (self.tmp / "x1.bin").unlink()
        (self.tmp / "x3.bin").unlink()
        with self.assertRaises(mva.MissingVolumeError) as cm:
            mva.open_volume_stream(self.tmp)
        gaps = cm.exception.gaps
        self.assertEqual([g.missing for g in gaps], [[1], [3]])
        self.assertIsNone(gaps[0].after_vol)
        self.assertEqual(gaps[0].before_vol, 2)
        self.assertEqual(gaps[0].byte_offset, 0)
        self.assertIsNone(gaps[1].before_vol)
        self.assertEqual(gaps[1].byte_offset, 10)

    def test_missing_contiguous_range(self):
        for i in range(1, 6):
            self._write_raw(i, 5, bytes([i]) * 7)
        for n in (2, 3, 4):
            (self.tmp / f"x{n}.bin").unlink()
        with self.assertRaises(mva.MissingVolumeError) as cm:
            mva.open_volume_stream(self.tmp)
        gap = cm.exception.gaps[0]
        self.assertEqual(gap.missing, [2, 3, 4])
        self.assertIn("2-4", gap.describe())
        self.assertEqual(gap.byte_offset, 7)

    def test_gap_blocks_reading_nothing_is_returned(self):
        self._make_three()
        (self.tmp / "x2.bin").unlink()
        with self.assertRaises(mva.MissingVolumeError):
            mva.VolumeReader(self.tmp)  # 构造即失败，不存在“跳过继续读”的 reader

    def test_find_gaps_directly(self):
        for i in range(1, 5):
            self._write_raw(i, 4, b"x" * (i))  # 卷长各不相同
        (self.tmp / "x3.bin").unlink()
        metas = mva.discover_volumes(self.tmp)
        gaps = mva.find_gaps(metas)
        self.assertEqual(gaps[0].missing, [3])
        # 卷1=1B, 卷2=2B -> 缺口逻辑偏移 3
        self.assertEqual(gaps[0].byte_offset, 3)


class TestInconsistency(_Tmp):
    def test_payload_longer_than_header_declares(self):
        p = self._write_raw(1, 1, b"abc", declared_len=2)
        with self.assertRaises(mva.InconsistentVolumeError) as cm:
            mva.parse_volume(p)
        self.assertIn("实际载荷为 3", str(cm.exception))

    def test_payload_shorter_due_to_truncation(self):
        p = self._write_raw(1, 1, b"abcdef", truncate=3)  # 文件被截断
        with self.assertRaises(mva.InconsistentVolumeError):
            mva.parse_volume(p)

    def test_bad_magic_and_short_file(self):
        bad = self.tmp / "bad.bin"
        bad.write_bytes(b"NOTMVAR" + b"\x00" * 20)
        with self.assertRaises(mva.BadVolumeHeaderError):
            mva.parse_volume(bad)
        short = self.tmp / "short.bin"
        short.write_bytes(b"MV")
        with self.assertRaises(mva.BadVolumeHeaderError):
            mva.parse_volume(short)

    def test_duplicate_volume_number(self):
        self._write_raw(1, 2, b"a", name="a.bin")
        self._write_raw(1, 2, b"a", name="b.bin")
        self._write_raw(2, 2, b"b", name="c.bin")
        with self.assertRaises(mva.InconsistentVolumeError):
            mva.discover_volumes(self.tmp)

    def test_inconsistent_total_in_headers(self):
        self._write_raw(1, 2, b"a")
        self._write_raw(2, 3, b"b")  # 自己声称总数 3
        with self.assertRaises(mva.InconsistentVolumeError):
            mva.discover_volumes(self.tmp)

    def test_bad_crc_detected_on_request(self):
        self._write_raw(1, 1, b"abc", crc=0xDEADBEEF)
        mva.parse_volume(self.tmp / "x1.bin")  # 默认不校验 CRC，不报错
        with self.assertRaises(mva.InconsistentVolumeError):
            mva.open_volume_stream(self.tmp, verify_crc=True)

    def test_varied_volume_lengths_still_concat_correctly(self):
        # 各卷长度不一致是正常情形：1, 0, 5, 3 字节
        payloads = {1: b"X", 2: b"", 3: b"hello", 4: b"end"}
        for no, body in payloads.items():
            self._write_raw(no, 4, body)
        with mva.open_volume_stream(self.tmp) as r:
            self.assertEqual(r.read(), b"X" + b"hello" + b"end")


class TestChunkedCrossVolume(_Tmp):
    """跨卷分块读取必须与整卷拼接一致（对拍，见 diff_check.py）。"""

    def test_chunked_read_matches_concat(self):
        data = os.urandom(1000)
        mva.write_volumes(data, self.tmp, max_data_len=64)
        with mva.open_volume_stream(self.tmp) as r:
            whole = r.read_all_concat()
        for chunk_size in (1, 2, 3, 7, 63, 64, 65, 127, 1024):
            with mva.open_volume_stream(self.tmp) as r:
                got = b"".join(iter(lambda: r.read(chunk_size), b""))
                self.assertEqual(got, whole, f"块大小 {chunk_size} 不一致")
                self.assertEqual(r.tell(), len(whole))

    def test_chunk_aligned_exactly_on_boundaries(self):
        data = b"".join(bytes([i]) * 10 for i in range(5))  # 50B，每卷 10B
        mva.write_volumes(data, self.tmp, max_data_len=10)
        with mva.open_volume_stream(self.tmp) as r:
            for _ in range(5):
                self.assertEqual(len(r.read(10)), 10)
            self.assertEqual(r.read(10), b"")

    def test_seek_and_resume(self):
        data = bytes(range(250))
        mva.write_volumes(data, self.tmp, max_data_len=37)  # 长度各异
        with mva.open_volume_stream(self.tmp) as r:
            r.seek(100)
            self.assertEqual(r.read(50), data[100:150])
            r.seek(-10, os.SEEK_END)
            self.assertEqual(r.read(), data[-10:])
            r.seek(0)
            self.assertEqual(r.read(1000), data)
            self.assertEqual(r.seek(0, os.SEEK_END), len(data))


if __name__ == "__main__":
    unittest.main(verbosity=2)
