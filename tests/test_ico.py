"""ICO 库自测：往返对拍、候选排序、损坏目录项、边界用例。"""

import struct
import unittest

from ico import IconFile, IconImage, IconFormatError, rank, pick_best
from tests.fixtures import (
    build_bmp_blob, build_png_blob, build_image, build_icon,
    SPECS_SINGLE, SPECS_MULTI, SPECS_ALPHA,
)

HEADER = struct.Struct("<HHH")
ENTRY = struct.Struct("<BBBBHHII")


def assemble_raw(count, packed_entries, blobs, header=None, extra_tail=b""):
    """手工拼装 ICO，允许故意制造不一致。

    packed_entries: ENTRY.pack(...) 列表；blobs: 与条目一一对应或共享。
    """
    if header is None:
        header = HEADER.pack(0, 1, count)
    return header + b"".join(packed_entries) + b"".join(blobs) + extra_tail


def make_entry(width, height, colors, bitcount, size, offset, reserved=0, planes=1):
    return ENTRY.pack(width % 256, height % 256, colors, reserved,
                      planes, bitcount, size, offset)


class RoundTripTests(unittest.TestCase):
    def assertRoundTrip(self, icon):
        data = icon.to_bytes()
        reparsed = IconFile.read(data)
        self.assertEqual(len(reparsed), len(icon))
        self.assertEqual(reparsed.to_bytes(), data)
        for original, got in zip(icon.images, reparsed.images):
            self.assertEqual((got.width, got.height, got.bpp,
                              got.compressed, got.colors_used, got.data),
                             (original.width, original.height, original.bpp,
                              original.compressed, original.colors_used, original.data))
        return data

    def test_single(self):
        self.assertRoundTrip(build_icon(SPECS_SINGLE))

    def test_multi_duplicates(self):
        icon = build_icon(SPECS_MULTI)
        data = self.assertRoundTrip(icon)
        parsed = IconFile.read(data)
        # 两个 16x16 重复尺寸、两个 48x48（BMP/PNG 各一）
        sizes = [(im.width, im.height) for im in parsed.images]
        self.assertEqual(sizes.count((16, 16)), 2)
        self.assertEqual(sizes.count((48, 48)), 2)

    def test_alpha_channel(self):
        icon = build_icon(SPECS_ALPHA)
        data = self.assertRoundTrip(icon)
        parsed = IconFile.read(data)
        self.assertTrue(all(im.bpp == 32 for im in parsed.images))
        # BMP 自底向上存储：透明像素 (0,0) 在 XOR 掩码最后一行的开头
        bmp = [im for im in parsed.images if not im.compressed][0]
        off = 40 + 32 * 4 * 31
        self.assertEqual(bmp.data[off:off + 4], b"\x00\x55\xaa\x00")

    def test_256_uses_zero_byte(self):
        icon = build_icon([dict(size=256, bpp=32, fmt="png"),
                           dict(size=256, bpp=32)])
        data = self.assertRoundTrip(icon)
        self.assertEqual(data[6], 0)   # 目录宽字节
        self.assertEqual(data[7], 0)   # 目录高字节

    def test_bpp_24_and_8(self):
        self.assertRoundTrip(build_icon([
            dict(size=32, bpp=24), dict(size=16, bpp=8)]))

    def test_empty_icon(self):
        self.assertEqual(IconFile([]).to_bytes(), b"\x00\x00\x01\x00\x00\x00")
        self.assertEqual(len(IconFile.read(b"\x00\x00\x01\x00\x00\x00")), 0)

    def test_noncanonical_layout_is_recanonicalized(self):
        # 数据间留空洞且乱序：规范写出后偏移重排，载荷逐字节保留
        icon = build_icon([dict(size=16, bpp=32), dict(size=48, bpp=32)])
        a, b = icon.images
        e1 = make_entry(16, 16, 0, 32, len(a.data), 64)
        e2 = make_entry(48, 48, 0, 32, len(b.data), 64 + len(a.data) + 128)
        raw = (HEADER.pack(0, 1, 2) + e1 + e2
               + b"\x00" * (64 - 38) + a.data
               + b"\x00" * 128 + b.data)
        parsed = IconFile.read(raw)
        canonical = parsed.to_bytes()
        # 再读一次仍一致；载荷顺序与内容不变
        self.assertEqual(IconFile.read(canonical).to_bytes(), canonical)
        self.assertEqual([im.data for im in IconFile.read(canonical).images],
                         [a.data, b.data])

    def test_shared_identical_region_ok(self):
        # 两个条目完全共享同一段数据：合法（经典多入口引用）
        icon = build_icon([dict(size=32, bpp=32)])
        blob = icon.images[0].data
        off = 6 + 2 * ENTRY.size
        e1 = make_entry(32, 32, 0, 32, len(blob), off)
        e2 = make_entry(32, 32, 0, 32, len(blob), off)
        raw = assemble_raw(2, [e1, e2], [blob])
        parsed = IconFile.read(raw)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed.images[0].data, parsed.images[1].data)


class RankTests(unittest.TestCase):
    def setUp(self):
        self.icon = build_icon(SPECS_MULTI)

    def test_exact_size_bpp_tie(self):
        images = self.icon.images
        # 目标 16：两个精确匹配(16@8, 16@32)，32bpp 胜出
        best = pick_best(images, 16)
        self.assertEqual((best.width, best.bpp), (16, 32))
        ranked = rank(images, 16)
        self.assertEqual((ranked[0].image.width, ranked[0].image.bpp), (16, 32))
        self.assertAlmostEqual(ranked[0].cost, 0.0)

    def test_format_tie_png_wins(self):
        images = self.icon.images
        best = pick_best(images, 48)
        self.assertEqual((best.width, best.bpp, best.compressed), (48, 32, True))

    def test_index_tie_is_deterministic(self):
        imgs = [build_image(dict(size=24, bpp=32)) for _ in range(3)]
        ranked = rank(imgs, 24)
        self.assertEqual([r.index for r in ranked], [0, 1, 2])
        self.assertEqual(ranked[0].image, pick_best(imgs, 24))

    def test_upscale_penalty(self):
        # 目标 22：16（放大 1.375，受惩罚）vs 32（缩小 1.45），32 应胜
        best = pick_best(self.icon.images, 22)
        self.assertEqual(best.width, 32)

    def test_downscale_order(self):
        # 目标 24：32 比 48 更近
        best = pick_best(self.icon.images, 24)
        self.assertEqual(best.width, 32)

    def test_target_256(self):
        best = pick_best(self.icon.images, 256)
        self.assertEqual((best.width, best.compressed), (256, True))

    def test_no_candidates(self):
        with self.assertRaises(IconFormatError):
            pick_best([], 32)

    def test_bad_target(self):
        with self.assertRaises(ValueError):
            rank(self.icon.images, 0)


class CorruptionTests(unittest.TestCase):
    def _expect_entry(self, raw, number, fragment):
        with self.assertRaises(IconFormatError) as ctx:
            IconFile.read(raw)
        msg = str(ctx.exception)
        self.assertIn("目录项 #%d" % number, msg)
        self.assertIn(fragment, msg)

    def test_dimension_mismatch(self):
        blob = build_bmp_blob(32, 32, 32)
        off = 6 + ENTRY.size
        entry = make_entry(16, 16, 0, 32, len(blob), off)  # 目录写 16，数据是 32
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "尺寸不一致")

    def test_bpp_mismatch(self):
        blob = build_bmp_blob(32, 32, 32)
        off = 6 + ENTRY.size
        entry = make_entry(32, 32, 0, 24, len(blob), off)
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "色深不一致")

    def test_palette_color_count_mismatch(self):
        blob = build_bmp_blob(16, 16, 8)
        off = 6 + ENTRY.size
        entry = make_entry(16, 16, 16, 8, len(blob), off)  # 实际 256
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "调色板颜色数不一致")

    def test_zero_size(self):
        off = 6 + ENTRY.size
        entry = make_entry(32, 32, 0, 32, 0, off)
        raw = assemble_raw(1, [entry], [])
        self._expect_entry(raw, 1, "数据长度为 0")

    def test_out_of_bounds(self):
        blob = build_bmp_blob(32, 32, 32)
        off = 6 + ENTRY.size
        entry = make_entry(32, 32, 0, 32, len(blob) + 100, off)
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "数据越界")

    def test_offset_into_directory(self):
        entry = make_entry(32, 32, 0, 32, 4, 6)
        raw = assemble_raw(1, [entry], [b"\x00\x00\x00\x00"])
        self._expect_entry(raw, 1, "落在目录区")

    def test_partial_overlap(self):
        icon = build_icon([dict(size=32, bpp=32), dict(size=16, bpp=32)])
        a, b = icon.images
        off = 6 + 2 * ENTRY.size
        e1 = make_entry(32, 32, 0, 32, len(a.data), off)
        e2 = make_entry(16, 16, 0, 32, len(b.data), off + 10)  # 部分重叠
        raw = assemble_raw(2, [e1, e2], [a.data + b.data])
        self._expect_entry(raw, 2, "部分重叠")

    def test_truncated_directory(self):
        with self.assertRaises(IconFormatError) as ctx:
            IconFile.read(HEADER.pack(0, 1, 5) + b"\x00" * 10)
        self.assertIn("目录不完整", str(ctx.exception))

    def test_bad_reserved_header(self):
        with self.assertRaises(IconFormatError) as ctx:
            IconFile.read(HEADER.pack(1, 1, 0))
        self.assertIn("ICONDIR.reserved", str(ctx.exception))

    def test_cursor_type_rejected(self):
        with self.assertRaises(IconFormatError) as ctx:
            IconFile.read(HEADER.pack(0, 2, 0))
        self.assertIn("type=2", str(ctx.exception))

    def test_entry_reserved_byte(self):
        blob = build_bmp_blob(32, 32, 32)
        off = 6 + ENTRY.size
        entry = make_entry(32, 32, 0, 32, len(blob), off, reserved=7)
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "保留字节")

    def test_unknown_payload(self):
        blob = b"GIF89a" + b"\x00" * 40
        off = 6 + ENTRY.size
        entry = make_entry(32, 32, 0, 32, len(blob), off)
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "无法识别")

    def test_png_bpp_mismatch(self):
        blob = build_png_blob(32, 32, rgba=True)
        off = 6 + ENTRY.size
        entry = make_entry(32, 32, 0, 24, len(blob), off)
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "色深不一致")

    def test_png_corrupt_crc(self):
        blob = bytearray(build_png_blob(16, 16, rgba=True))
        blob[30] ^= 0xFF  # 破坏 IDAT 区域（CRC/数据）
        blob = bytes(blob)
        off = 6 + ENTRY.size
        entry = make_entry(16, 16, 0, 32, len(blob), off)
        raw = assemble_raw(1, [entry], [blob])
        # CRC 被破坏时报 CRC 失败；若改到压缩流则至少要报 PNG 损坏
        with self.assertRaises(IconFormatError) as ctx:
            IconFile.read(raw)
        self.assertIn("目录项 #1", str(ctx.exception))

    def test_truncated_png(self):
        blob = build_png_blob(16, 16, rgba=True)[:20]
        off = 6 + ENTRY.size
        entry = make_entry(16, 16, 0, 32, len(blob), off)
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "PNG 数据损坏")

    def test_bmp_short(self):
        blob = build_bmp_blob(32, 32, 32)[:30]
        off = 6 + ENTRY.size
        entry = make_entry(32, 32, 0, 32, len(blob), off)
        raw = assemble_raw(1, [entry], [blob])
        self._expect_entry(raw, 1, "BMP")

    def test_writer_rejects_metadata_mismatch(self):
        blob = build_bmp_blob(32, 32, 32)
        img = IconImage(16, 16, 32, blob)  # 声明 16，载荷 32
        with self.assertRaises(IconFormatError) as ctx:
            IconFile([img]).to_bytes()
        self.assertIn("目录项 #1", str(ctx.exception))

    def test_writer_rejects_bad_blob(self):
        with self.assertRaises(IconFormatError):
            IconImage.from_blob(b"nonsense data here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
