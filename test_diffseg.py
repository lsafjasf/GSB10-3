#!/usr/bin/env python3
"""diffseg 自测：全量覆盖 / 无变化 / 只改尾部 / 段重叠冲突 / 基准缺失 / 空洞。"""
import json
import unittest

from diffseg import (
    Backup,
    ChainError,
    DigestMismatchError,
    HoleError,
    MissingBaseError,
    Segment,
    SegmentOverlapError,
    compute_diff,
    restore,
    sha256_hex,
)


def make_backup(base_version, target_version, target, segments):
    return Backup(base_version, target_version, len(target), sha256_hex(target), segments)


class TestFullCoverage(unittest.TestCase):
    """全量覆盖：每个字节都变化，段覆盖整个文件。"""

    def test_full_coverage(self):
        base = b"\x00" * 64
        target = b"\xff" * 64
        bk = compute_diff(base, target, "v1", "v2")
        self.assertEqual(len(bk.segments), 1)
        self.assertEqual((bk.segments[0].offset, bk.segments[0].length), (0, 64))
        got, _ = restore(base, [bk])
        self.assertEqual(got, target)
        self.assertEqual(len(got), len(target))

    def test_full_coverage_without_base(self):
        # 段已全覆盖时，即使基准缺失也能恢复
        target = bytes(range(1, 101))  # 与全零基准逐字节不同 -> 段全覆盖
        bk = compute_diff(b"\x00" * 100, target, "v1", "v2")
        got, _ = restore(None, [bk])
        self.assertEqual(got, target)


class TestNoChange(unittest.TestCase):
    """无变化：零段，恢复结果等于基准。"""

    def test_no_change(self):
        base = b"hello world, nothing changed" * 3
        bk = compute_diff(base, base, "v1", "v2")
        self.assertEqual(bk.segments, [])
        got, report = restore(base, [bk])
        self.assertEqual(got, base)
        self.assertEqual(report.applied_segments, [])
        self.assertEqual(report.overwritten, [])


class TestTailOnlyChange(unittest.TestCase):
    """只改尾部：追加 + 修改末尾字节，段只落在尾部。"""

    def test_tail_append(self):
        base = b"A" * 100
        target = base + b"TAIL-APPENDED"
        bk = compute_diff(base, target, "v1", "v2")
        self.assertEqual(len(bk.segments), 1)
        self.assertEqual(bk.segments[0].offset, 100)
        got, _ = restore(base, [bk])
        self.assertEqual(got, target)

    def test_tail_modify(self):
        base = bytearray(b"B" * 200)
        target = bytes(base[:190]) + b"CHANGED!!?"  # 只动最后 10 字节
        bk = compute_diff(bytes(base), target, "v1", "v2")
        for seg in bk.segments:
            self.assertGreaterEqual(seg.offset, 190)
        got, _ = restore(bytes(base), [bk])
        self.assertEqual(got, target)
        self.assertEqual(len(got), 200)


class TestOverlapConflict(unittest.TestCase):
    """段重叠冲突：同备份内拒绝；跨备份最新优先并报告被覆盖段。"""

    BASE = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # 36 字节

    def test_intra_backup_overlap_rejected(self):
        target = self.BASE[:8] + b"x" * 24 + self.BASE[32:]
        segs = [
            Segment.from_data("v1", 8, b"a" * 16),   # [8, 24)
            Segment.from_data("v1", 16, b"b" * 16),  # [16, 32) 与前段重叠
        ]
        bk = make_backup("v1", "v2", target, segs)
        with self.assertRaises(SegmentOverlapError):
            bk.validate()
        with self.assertRaises(SegmentOverlapError):
            Backup.from_json(bk.to_json())

    def test_cross_backup_latest_wins(self):
        seg_a = Segment.from_data("v1", 8, b"a" * 16)   # v2: [8, 24)
        v2 = self.BASE[:8] + seg_a.data + self.BASE[24:]
        seg_b = Segment.from_data("v2", 12, b"B" * 16)  # v3: [12, 28)，覆盖 A 的 [12,24)
        v3 = v2[:12] + seg_b.data + v2[28:]
        bk2 = make_backup("v1", "v2", v2, [seg_a])
        bk3 = make_backup("v2", "v3", v3, [seg_b])

        got, report = restore(self.BASE, [bk2, bk3])
        self.assertEqual(got, v3)  # 逐字节一致
        # 最新优先：v3 的段覆盖 v2 段的 [12, 24)
        self.assertEqual(len(report.overwritten), 1)
        rec = report.overwritten[0]
        self.assertEqual(rec.covered_range, (12, 24))
        self.assertTrue(rec.covered_label.startswith("v2#"))
        self.assertTrue(rec.by_label.startswith("v3#"))
        # 重叠区内容来自最新段 B
        self.assertEqual(got[12:28], b"B" * 16)
        self.assertEqual(got[8:12], b"a" * 4)


class TestMissingBase(unittest.TestCase):
    """基准缺失：段未全覆盖时必须显式报错，不得填零。"""

    def test_missing_base(self):
        target = b"0123456789"
        segs = [Segment.from_data("v1", 0, b"01234")]  # 只覆盖 [0,5)
        bk = make_backup("v1", "v2", target, segs)
        with self.assertRaises(MissingBaseError) as ctx:
            restore(None, [bk])
        self.assertIn("v1", str(ctx.exception))

    def test_chain_broken(self):
        base = b"aaaaaaaa"
        v2 = b"aaaaBBBB"
        bk1 = compute_diff(base, v2, "v1", "v2")
        bk_bad = compute_diff(base, b"CCCCaaaa", "vX", "v3")  # 基准版本接不上
        with self.assertRaises(ChainError):
            restore(base, [bk1, bk_bad])


class TestHoles(unittest.TestCase):
    """空洞：显式标记区间并抛错，绝不填零。"""

    def test_hole_explicit(self):
        base = b"0123456789"  # 覆盖 [0,10)
        target = base + b"??????????"  # 目标长度 20，[10,20) 无段覆盖
        bk = make_backup("v1", "v2", target, [])
        with self.assertRaises(HoleError) as ctx:
            restore(base, [bk])
        self.assertEqual(ctx.exception.holes, [(10, 20)])
        self.assertIn("[10, 20)", str(ctx.exception))

    def test_hole_in_middle(self):
        base = b"0123456789"
        target = b"01234" + b"#####" + b"#####"  # 长度 15，[10,15) 空洞
        segs = [Segment.from_data("v1", 5, b"#####")]  # 覆盖 [5,10)
        bk = make_backup("v1", "v2", target, segs)
        with self.assertRaises(HoleError) as ctx:
            restore(base, [bk])
        self.assertEqual(ctx.exception.holes, [(10, 15)])


class TestDigests(unittest.TestCase):
    """摘要校验：段内容被篡改必须检出。"""

    def test_segment_tampered(self):
        bk = compute_diff(b"A" * 32, b"B" * 32, "v1", "v2")
        d = json.loads(bk.to_json())
        raw = bytearray(__import__("base64").b64decode(d["segments"][0]["data_b64"]))
        raw[0] ^= 0xFF
        d["segments"][0]["data_b64"] = __import__("base64").b64encode(bytes(raw)).decode()
        with self.assertRaises(DigestMismatchError):
            Backup.from_json(json.dumps(d))

    def test_wrong_base_detected(self):
        # 段只覆盖前 5 字节，其余必须来自基准；给错基准 -> 整体摘要不符
        target2 = b"12345" + b"A" * 27
        bk2 = make_backup("v1", "v2", target2,
                          [Segment.from_data("v1", 0, b"12345")])
        with self.assertRaises(DigestMismatchError):
            restore(b"X" * 32, [bk2])


class TestShiftAndTruncate(unittest.TestCase):
    """中部插入/删除导致位移、文件截断。"""

    def test_insert_middle(self):
        base = b"header-" + b"M" * 50 + b"-footer"
        target = b"header-" + b"INSERTED-" + b"M" * 50 + b"-footer"
        bk = compute_diff(base, target, "v1", "v2")
        got, _ = restore(base, [bk])
        self.assertEqual(got, target)

    def test_delete_middle(self):
        base = b"header-" + b"TO-BE-DELETED-" + b"M" * 50 + b"-footer"
        target = b"header-" + b"M" * 50 + b"-footer"
        bk = compute_diff(base, target, "v1", "v2")
        got, _ = restore(base, [bk])
        self.assertEqual(got, target)

    def test_truncate(self):
        base = b"X" * 100
        target = b"X" * 40
        bk = compute_diff(base, target, "v1", "v2")
        self.assertEqual(bk.segments, [])
        got, _ = restore(base, [bk])
        self.assertEqual(got, target)
        self.assertEqual(len(got), 40)

    def test_empty_to_nonempty_and_back(self):
        bk1 = compute_diff(b"", b"abc", "v1", "v2")
        bk2 = compute_diff(b"abc", b"", "v2", "v3")
        got, _ = restore(b"", [bk1])
        self.assertEqual(got, b"abc")
        got, _ = restore(b"", [bk1, bk2])
        self.assertEqual(got, b"")


class TestJsonRoundTrip(unittest.TestCase):
    def test_manifest_roundtrip(self):
        base = b"The quick brown fox jumps over the lazy dog"
        target = base[:10] + b"QUICK" + base[15:30] + b"!" * 7
        bk = compute_diff(base, target, "v1", "v2")
        bk2 = Backup.from_json(bk.to_json())
        self.assertEqual(bk, bk2)
        got, _ = restore(base, [bk2])
        self.assertEqual(got, target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
