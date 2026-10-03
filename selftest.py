"""mvarchive 自测：python3 selftest.py"""

import io
import os
import random
import struct
import tempfile
import unittest

from mvarchive import (
    HEADER_SIZE,
    MAGIC,
    CorruptVolumeError,
    MissingVolumeError,
    MultiVolumeReader,
    write_volume,
)


def make_data(length: int, seed: int = 1234) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.getrandbits(8) for _ in range(length))


def split_parts(data: bytes, sizes):
    parts, pos = [], 0
    for size in sizes:
        parts.append(data[pos:pos + size])
        pos += size
    return parts


class ArchiveTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def path(self, name):
        return os.path.join(self.root, name)

    def build(self, parts, names=None, present=None, total=None):
        """parts: 各卷负载；names: 各卷文件名；present: 要写哪几个（0基）。"""
        n = len(parts)
        total = total or n
        names = names or ["part_%02d.mvol" % (i + 1) for i in range(n)]
        present = present if present is not None else range(n)
        paths = []
        for i in present:
            p = self.path(names[i])
            write_volume(p, i + 1, total, parts[i])
            paths.append(p)
        return paths

    def read_all(self, paths):
        with MultiVolumeReader(paths) as reader:
            return reader.read()

    def read_chunked(self, paths, chunk):
        with MultiVolumeReader(paths) as reader:
            return b"".join(reader.iter_chunks(chunk))


class TestBasicAndOrdering(ArchiveTestBase):
    def test_single_volume(self):
        data = make_data(500, seed=1)
        paths = self.build(split_parts(data, [500]))
        self.assertEqual(self.read_all(paths), data)

    def test_shuffled_filenames_sorted_by_volume_number(self):
        # 7 卷，长度不规整；文件名故意按字典序与卷号顺序不同
        sizes = [100, 300, 7, 250, 1, 333, 243]
        data = make_data(sum(sizes), seed=2)
        # names[i] 是卷 i+1 的文件名；字典序 v1 < v10 < v2 < ... 与卷号序不同
        names = ["v2.mvol", "v10.mvol", "v1.mvol", "v7.mvol",
                 "v3.mvol", "v9.mvol", "v5.mvol"]
        parts = split_parts(data, sizes)
        paths = self.build(parts, names=names)
        # 文件名在字典序下的拼接（错误参考）与逻辑结果不同
        lex_order = b"".join(part for _, part in sorted(zip(names, parts)))
        with MultiVolumeReader(paths) as reader:
            self.assertEqual(reader.volumes[0].source, self.path("v2.mvol"))
            result = reader.read()
        self.assertEqual(result, data)
        self.assertNotEqual(result, lex_order)


class TestGapDetection(ArchiveTestBase):
    def test_middle_volume_missing(self):
        sizes = [100, 200, 300, 200, 100]
        data = make_data(sum(sizes), seed=3)
        paths = self.build(split_parts(data, sizes), present=[0, 1, 3, 4], total=5)
        with self.assertRaises(MissingVolumeError) as ctx:
            MultiVolumeReader(paths)
        err = ctx.exception
        self.assertEqual(err.missing_volumes, (3,))
        self.assertEqual(err.gaps[0].first, 3)
        self.assertEqual(err.gaps[0].last, 3)
        # 缺口位于卷1、卷2之后 => 偏移 300
        self.assertEqual(err.gaps[0].byte_offset, 300)
        self.assertEqual(err.total, 5)

    def test_multiple_gaps_and_runs(self):
        # 总长 10 卷，缺 2-3（连续段）、7（单卷）
        sizes = [10] * 10
        data = make_data(sum(sizes), seed=4)
        present = [0, 3, 4, 5, 7, 8, 9]  # 卷号 1,4,5,6,8,9,10
        paths = self.build(split_parts(data, sizes), present=present, total=10)
        with self.assertRaises(MissingVolumeError) as ctx:
            MultiVolumeReader(paths)
        gaps = ctx.exception.gaps
        self.assertEqual([(g.first, g.last, g.byte_offset) for g in gaps],
                         [(2, 3, 10), (7, 7, 40)])
        self.assertEqual(ctx.exception.missing_volumes, (2, 3, 7))

    def test_first_and_last_volume_missing(self):
        sizes = [50, 60, 70]
        data = make_data(sum(sizes), seed=5)
        paths_first = self.build(split_parts(data, sizes), present=[1, 2], total=3)
        with self.assertRaises(MissingVolumeError) as ctx:
            MultiVolumeReader(paths_first)
        self.assertEqual(ctx.exception.missing_volumes, (1,))
        self.assertEqual(ctx.exception.gaps[0].byte_offset, 0)

        paths_last = self.build(split_parts(data, sizes), present=[0, 1], total=3)
        with self.assertRaises(MissingVolumeError) as ctx:
            MultiVolumeReader(paths_last)
        self.assertEqual(ctx.exception.missing_volumes, (3,))
        # 缺口在卷1+卷2之后 => 110
        self.assertEqual(ctx.exception.gaps[0].byte_offset, 110)

    def test_missing_raises_before_any_data_read(self):
        # 绝不静默跳过：构造期即失败，没有可用 reader
        sizes = [10, 10, 10]
        data = make_data(30, seed=6)
        paths = self.build(split_parts(data, sizes), present=[0, 2], total=3)
        with self.assertRaises(MissingVolumeError):
            MultiVolumeReader(paths)


class TestCorruption(ArchiveTestBase):
    def _raw_rewrite(self, name, volume_number, total, payload, declared_len=None):
        declared_len = declared_len if declared_len is not None else len(payload)
        with open(self.path(name), "wb") as fh:
            fh.write(struct.pack(">4sBIIQ", MAGIC, 1, volume_number, total, declared_len))
            fh.write(payload)

    def test_payload_length_inconsistent_with_header(self):
        # 卷内长度不一致：头声明 100，实际 90
        self._raw_rewrite("bad.mvol", 1, 2, b"x" * 90, declared_len=100)
        write_volume(self.path("ok.mvol"), 2, 2, b"y" * 10)
        with self.assertRaises(CorruptVolumeError) as ctx:
            MultiVolumeReader([self.path("bad.mvol"), self.path("ok.mvol")])
        self.assertIn("卷内长度不一致", str(ctx.exception))

    def test_payload_longer_than_declared(self):
        self._raw_rewrite("bad.mvol", 1, 1, b"x" * 11, declared_len=10)
        with self.assertRaises(CorruptVolumeError):
            MultiVolumeReader([self.path("bad.mvol")])

    def test_duplicate_volume_number(self):
        p1 = self.path("a.mvol")
        p2 = self.path("b.mvol")
        write_volume(p1, 1, 2, b"aa")
        write_volume(p2, 1, 2, b"bb")
        with self.assertRaises(CorruptVolumeError) as ctx:
            MultiVolumeReader([p1, p2])
        self.assertIn("卷号重复", str(ctx.exception))

    def test_inconsistent_total(self):
        p1 = self.path("a.mvol")
        p2 = self.path("b.mvol")
        write_volume(p1, 1, 3, b"aa")
        write_volume(p2, 2, 2, b"bb")
        with self.assertRaises(CorruptVolumeError):
            MultiVolumeReader([p1, p2])

    def test_bad_magic_and_version(self):
        with open(self.path("bad.mvol"), "wb") as fh:
            fh.write(struct.pack(">4sBIIQ", b"XXXX", 1, 1, 1, 1) + b"z")
        with self.assertRaises(CorruptVolumeError):
            MultiVolumeReader([self.path("bad.mvol")])

        with open(self.path("ver.mvol"), "wb") as fh:
            fh.write(struct.pack(">4sBIIQ", MAGIC, 9, 1, 1, 1) + b"z")
        with self.assertRaises(CorruptVolumeError):
            MultiVolumeReader([self.path("ver.mvol")])

    def test_truncated_header(self):
        with open(self.path("short.mvol"), "wb") as fh:
            fh.write(MAGIC)
        with self.assertRaises(CorruptVolumeError):
            MultiVolumeReader([self.path("short.mvol")])

    def test_number_out_of_range(self):
        with open(self.path("oob.mvol"), "wb") as fh:
            fh.write(struct.pack(">4sBIIQ", MAGIC, 1, 3, 2, 0))
        with self.assertRaises(CorruptVolumeError):
            MultiVolumeReader([self.path("oob.mvol")])


class TestChunkedReads(ArchiveTestBase):
    def _build_uneven(self, seed=7):
        # 刻意让各卷长度互不相同且出现 0/1 长度
        sizes = [0, 1, 7, 0, 99, 1, 1024, 3, 0]
        data = make_data(sum(sizes), seed=seed)
        paths = self.build(split_parts(data, sizes))
        return data, paths, sizes

    def test_chunk_reads_equivalent_to_full_read(self):
        data, paths, _ = self._build_uneven()
        for chunk in (1, 2, 3, 7, 64, 100, 255, 1024, 4096, 1 << 20):
            with MultiVolumeReader(paths) as reader:
                got = b"".join(reader.iter_chunks(chunk))
            self.assertEqual(got, data, "块大小 %d 对拍失败" % chunk)

    def test_chunk_crosses_every_boundary(self):
        # 从每个逻辑偏移起读一个能跨越至少一个卷边界的窗口
        data, paths, sizes = self._build_uneven()
        bounds = {0}
        pos = 0
        for size in sizes:
            pos += size
            bounds.add(pos)
        for offset in sorted(bounds):
            for length in (1, 2, 5, 2000):
                with MultiVolumeReader(paths) as reader:
                    got = reader.read_window(offset, length)
                self.assertEqual(got, data[offset:offset + length],
                                 "窗口 (%d,%d) 对拍失败" % (offset, length))

    def test_staggered_offsets_inside_volumes(self):
        data, paths, _ = self._build_uneven()
        for offset in (4, 5, 6, 8, 100, 1050):
            if offset > len(data):
                continue
            with MultiVolumeReader(paths) as reader:
                got = reader.read_window(offset, 37)
            self.assertEqual(got, data[offset:offset + 37])

    def test_partial_reads_sizes_sum_to_stream(self):
        # 非等长多次 read 的累计结果与整流一致
        data, paths, _ = self._build_uneven()
        pattern = [3, 1, 10, 2, 77, 500, 4096]
        with MultiVolumeReader(paths) as reader:
            got, i = b"", 0
            while True:
                chunk = reader.read(pattern[i % len(pattern)])
                i += 1
                if not chunk:
                    break
                got += chunk
        self.assertEqual(got, data)

    def test_eof_and_tell_and_size_zero(self):
        data, paths, _ = self._build_uneven()
        with MultiVolumeReader(paths) as reader:
            self.assertEqual(reader.logical_size, len(data))
            self.assertEqual(reader.tell(), 0)
            self.assertEqual(reader.read(0), b"")
            self.assertEqual(reader.tell(), 0)
            first = reader.read(10)
            self.assertEqual(first, data[:10])
            self.assertEqual(reader.tell(), 10)
            rest = reader.read()
            self.assertEqual(rest, data[10:])
            self.assertEqual(reader.tell(), len(data))
            self.assertEqual(reader.read(5), b"")

    def test_single_byte_stream(self):
        data = b"Q"
        paths = self.build([data])
        with MultiVolumeReader(paths) as reader:
            self.assertEqual(reader.read(1), b"Q")
            self.assertEqual(reader.read(1), b"")

    def test_empty_archive_stream(self):
        # 多卷但全部空负载
        paths = self.build([b"", b"", b""])
        with MultiVolumeReader(paths) as reader:
            self.assertEqual(reader.logical_size, 0)
            self.assertEqual(reader.read(), b"")
            self.assertEqual(list(reader.iter_chunks(16)), [])

    def test_bad_iter_chunk_size(self):
        paths = self.build([b"abc"])
        with MultiVolumeReader(paths) as reader:
            with self.assertRaises(ValueError):
                list(reader.iter_chunks(0))


class TestMisc(ArchiveTestBase):
    def test_no_sources(self):
        from mvarchive import MVArchiveError
        with self.assertRaises(MVArchiveError):
            MultiVolumeReader([])

    def test_header_reader_for_file_like(self):
        # write/read 也支持文件对象
        buf = io.BytesIO()
        write_volume(buf, 1, 1, b"hi")
        buf.seek(0)
        from mvarchive import read_header
        info = read_header(buf)
        self.assertEqual((info.volume_number, info.total_volumes, info.payload_length),
                         (1, 1, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
