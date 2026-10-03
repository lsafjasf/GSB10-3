#!/usr/bin/env python3
"""test_icoformat.py — 边界用例自测（unittest，仅标准库）。

覆盖：单尺寸、多尺寸重复、带透明通道、目录项损坏（偏移越界 / 长度越界 /
区间重叠 / 尺寸色深不符 / 头部损坏），以及往返逐字节一致与排序确定性。
"""
import struct
import unittest

import icoformat
from icoformat import IconFormatError, IconImage
from icon_samples import make_bmp, make_png


def build_blob(images):
    return icoformat.build(images)


class TestParse(unittest.TestCase):
    def test_single_size(self):
        blob = build_blob([IconImage(16, 16, 32, make_bmp(16, 16, 32))])
        icon = icoformat.parse(blob)
        self.assertEqual(len(icon.images), 1)
        img = icon.images[0]
        self.assertEqual((img.width, img.height, img.bit_count), (16, 16, 32))
        self.assertEqual(img.offset, 6 + 16)
        self.assertEqual(img.size, len(img.data))
        self.assertFalse(img.compressed)

    def test_multi_duplicate_sizes(self):
        blob = build_blob([
            IconImage(32, 32, 24, make_bmp(32, 32, 24)),
            IconImage(32, 32, 32, make_bmp(32, 32, 32, alpha_mode="sparse")),
            IconImage(32, 32, 8, make_bmp(32, 32, 8)),
        ])
        icon = icoformat.parse(blob)
        self.assertEqual([im.bit_count for im in icon.images], [24, 32, 8])
        # 同尺寸不同色深：缩放代价相同 -> 高色深优先
        best = icon.select(32)
        self.assertEqual(best.bit_count, 32)

    def test_alpha_channel(self):
        blob = build_blob([
            IconImage(32, 32, 32, make_bmp(32, 32, 32, alpha_mode="sparse")),
            IconImage(32, 32, 32, make_bmp(32, 32, 32, alpha_mode="opaque")),
            IconImage(64, 64, 32, make_png(64, 64, with_alpha=True)),
        ])
        icon = icoformat.parse(blob)
        self.assertTrue(icon.images[0].has_alpha)
        self.assertFalse(icon.images[1].has_alpha)
        self.assertTrue(icon.images[2].has_alpha)
        self.assertTrue(icon.images[2].compressed)

    def test_png_256_size(self):
        blob = build_blob([IconImage(256, 256, 32, make_png(256, 256))])
        icon = icoformat.parse(blob)
        self.assertEqual((icon.images[0].width, icon.images[0].height), (256, 256))
        # 目录项中 256 应编码为 0
        self.assertEqual(blob[6], 0)
        self.assertEqual(blob[7], 0)


class TestCorruptDirectory(unittest.TestCase):
    def setUp(self):
        self.images = [
            IconImage(16, 16, 32, make_bmp(16, 16, 32)),
            IconImage(32, 32, 32, make_bmp(32, 32, 32)),
        ]
        self.blob = bytearray(build_blob(self.images))

    def _entry(self, i):
        return 6 + 16 * i

    def test_offset_out_of_range(self):
        # 把 entry #1 的偏移改到文件末尾之外
        struct.pack_into("<I", self.blob, self._entry(1) + 12, 10 ** 9)
        with self.assertRaises(IconFormatError) as ctx:
            icoformat.parse(bytes(self.blob))
        self.assertIn("entry #1", str(ctx.exception))

    def test_size_out_of_range(self):
        # 把 entry #0 的长度改到超出文件
        struct.pack_into("<I", self.blob, self._entry(0) + 8, 10 ** 9)
        with self.assertRaises(IconFormatError) as ctx:
            icoformat.parse(bytes(self.blob))
        self.assertIn("entry #0", str(ctx.exception))

    def test_overlapping_ranges(self):
        # 让 entry #1 的偏移与 entry #0 重叠
        off0 = struct.unpack_from("<I", self.blob, self._entry(0) + 12)[0]
        struct.pack_into("<I", self.blob, self._entry(1) + 12, off0 + 4)
        with self.assertRaises(IconFormatError) as ctx:
            icoformat.parse(bytes(self.blob))
        msg = str(ctx.exception)
        self.assertIn("entry #1", msg)
        self.assertIn("entry #0", msg)

    def test_size_mismatch_with_data(self):
        # 目录声明 16x16，但 entry #0 的数据实际是 32x32
        blob = build_blob([
            IconImage(32, 32, 32, make_bmp(32, 32, 32)),
            IconImage(16, 16, 32, make_bmp(16, 16, 32)),
        ])
        blob = bytearray(blob)
        blob[self._entry(0)] = 16   # 宽改成 16
        blob[self._entry(0) + 1] = 16
        with self.assertRaises(IconFormatError) as ctx:
            icoformat.parse(bytes(blob))
        self.assertIn("entry #0", str(ctx.exception))

    def test_bitcount_mismatch_with_data(self):
        blob = bytearray(build_blob(
            [IconImage(16, 16, 32, make_bmp(16, 16, 32))]))
        struct.pack_into("<H", blob, self._entry(0) + 6, 24)  # 目录写 24bpp
        with self.assertRaises(IconFormatError) as ctx:
            icoformat.parse(bytes(blob))
        self.assertIn("entry #0", str(ctx.exception))

    def test_offset_inside_directory(self):
        struct.pack_into("<I", self.blob, self._entry(1) + 12, 8)
        with self.assertRaises(IconFormatError) as ctx:
            icoformat.parse(bytes(self.blob))
        self.assertIn("entry #1", str(ctx.exception))

    def test_bad_header(self):
        for mutate, frag in (
            (lambda b: struct.pack_into("<H", b, 0, 7), "reserved"),
            (lambda b: struct.pack_into("<H", b, 2, 9), "类型"),
            (lambda b: struct.pack_into("<H", b, 4, 0), "数量为 0"),
        ):
            blob = bytearray(self.blob)
            mutate(blob)
            with self.assertRaises(IconFormatError) as ctx:
                icoformat.parse(bytes(blob))
            self.assertIn(frag, str(ctx.exception))

    def test_truncated_file(self):
        with self.assertRaises(IconFormatError):
            icoformat.parse(bytes(self.blob[:10]))
        with self.assertRaises(IconFormatError):
            icoformat.parse(b"\x00\x00")


class TestRoundTrip(unittest.TestCase):
    def test_byte_identical_roundtrip(self):
        images = [
            IconImage(16, 16, 32, make_bmp(16, 16, 32)),
            IconImage(32, 32, 24, make_bmp(32, 32, 24)),
            IconImage(32, 32, 32, make_bmp(32, 32, 32, alpha_mode="sparse")),
            IconImage(48, 48, 8, make_bmp(48, 48, 8)),
            IconImage(256, 256, 32, make_png(256, 256)),
        ]
        blob1 = icoformat.build(images)
        blob2 = icoformat.parse(blob1).to_bytes()
        self.assertEqual(blob1, blob2)
        # 偏移与长度自洽：目录区后顺序排布、互不重叠、不越界
        icon = icoformat.parse(blob1)
        expect = 6 + 16 * len(icon.images)
        for im in icon.images:
            self.assertEqual(im.offset, expect)
            self.assertEqual(im.size, len(im.data))
            expect += im.size
        self.assertEqual(expect, len(blob1))


class TestRanking(unittest.TestCase):
    def setUp(self):
        self.images = [
            IconImage(16, 16, 32, make_bmp(16, 16, 32)),
            IconImage(24, 24, 24, make_bmp(24, 24, 24)),
            IconImage(32, 32, 24, make_bmp(32, 32, 24)),
            IconImage(32, 32, 32, make_bmp(32, 32, 32)),
            IconImage(48, 48, 8, make_bmp(48, 48, 8)),
        ]

    def test_exact_match_first(self):
        self.assertEqual(icoformat.select(self.images, 32).bit_count, 32)

    def test_downscale_preferred_over_upscale(self):
        # 目标 40：48 缩小优先于 32 放大
        best = icoformat.select(self.images, 40)
        self.assertEqual(best.width, 48)

    def test_deterministic_tiebreak(self):
        a = IconImage(32, 32, 32, make_bmp(32, 32, 32))
        b = IconImage(32, 32, 32, make_bmp(32, 32, 32))
        for _ in range(10):
            self.assertIs(icoformat.select([a, b], 32), a)
            self.assertIs(icoformat.select([b, a], 32), b)  # 列表位置即序号

    def test_invalid_target(self):
        with self.assertRaises(ValueError):
            icoformat.rank(self.images, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
