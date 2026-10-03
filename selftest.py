"""差异段编码与恢复 —— 边界用例自测（python3 selftest.py）。

覆盖：全量、无变化、只改尾部（改尾/追加/截断）、段重叠冲突、
基准缺失（缺内容 + 链断裂）、同包段互相覆盖、摘要被篡改、空文件、
重叠样例数据回放、随机往返。
"""

import json
import os
import random
import unittest

import diffseg
from diffseg import (Backup, Segment, ChainError, DigestMismatchError,
                     MissingBaseError, ValidationError, encode, restore)

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAP_DIR = os.path.join(HERE, "data", "overlap_case")


class EncodeRestoreTests(unittest.TestCase):
    def test_full_coverage(self):
        # 全量：每个块都与基准不同，段应完整覆盖目标文件。
        base = bytes(10000)
        target = bytes((i * 37) % 256 for i in range(10000))
        backup = encode(base, target, "v0", "v1", block_size=256)
        # 连续变化的块会合并，最终是一个覆盖全文件的大段。
        self.assertEqual(len(backup.segments), 1)
        self.assertEqual((backup.segments[0].offset, backup.segments[0].length),
                         (0, len(target)))
        covered = sum(s.length for s in backup.segments)
        self.assertEqual(covered, len(target))
        # 全覆盖时即使没有基准内容也能恢复。
        data, report = restore(None, [backup])
        self.assertEqual(data, target)
        self.assertEqual(report.holes, [])
        self.assertTrue(report.digest_ok)
        with self.assertRaises(MissingBaseError):
            restore(None, [encode(base, b"\x00" * 9999 + b"\x01", "v0", "v1",
                                  block_size=256)])

    def test_no_change(self):
        base = os.urandom(20000)
        backup = encode(base, base, "v0", "v1")
        self.assertEqual(backup.segments, [])
        self.assertEqual(backup.target_length, len(base))
        data, report = restore(base, [backup])
        self.assertEqual(data, base)
        self.assertEqual(report.holes[0].length, len(base))
        # 序列化往返
        again = diffseg.loads_backup(diffseg.dumps_backup(backup))
        self.assertEqual(diffseg.backup_to_dict(again),
                         diffseg.backup_to_dict(backup))

    def test_tail_only(self):
        base = bytes((i * 13 + 1) % 256 for i in range(8192))
        # 1) 只改尾部少量字节
        target1 = base[:-16] + b"\xff" * 16
        b1 = encode(base, target1, "v0", "v1", block_size=4096)
        self.assertEqual(len(b1.segments), 1)
        self.assertGreaterEqual(b1.segments[0].offset, 4096)
        self.assertEqual(restore(base, [b1])[0], target1)
        # 2) 尾部追加
        target2 = target1 + b"append-tail!" * 10
        b2 = encode(target1, target2, "v1", "v2", block_size=4096)
        self.assertEqual(restore(base, [b1, b2])[0], target2)
        # 3) 尾部截断
        target3 = target2[:100]
        b3 = encode(target2, target3, "v2", "v3", block_size=4096)
        self.assertEqual(b3.segments, [])
        data, _ = restore(base, [b1, b2, b3])
        self.assertEqual(len(data), 100)
        self.assertEqual(data, target3)

    def test_empty_file(self):
        base = b""
        target = b"hello"
        b1 = encode(base, target, "v0", "v1", block_size=4)
        self.assertEqual(restore(base, [b1])[0], target)
        b2 = encode(target, b"", "v1", "v2", block_size=4)
        self.assertEqual(b2.segments, [])
        self.assertEqual(b2.target_length, 0)
        data, _ = restore(base, [b1, b2])
        self.assertEqual(data, b"")

    def test_segment_fields(self):
        # 差异段必须带基准版本、偏移、长度、内容摘要。
        backup = encode(b"abcdef", b"abXYef", "base-7", "v9", block_size=2)
        seg = backup.segments[0]
        self.assertEqual(seg.base_version, "base-7")
        self.assertEqual((seg.offset, seg.length), (2, 2))
        raw = diffseg.backup_to_dict(backup)["segments"][0]
        self.assertEqual(set(raw), {"base_version", "offset", "length",
                                    "sha256", "data_b64"})
        self.assertEqual(raw["sha256"], seg.digest)


class OverlapTests(unittest.TestCase):
    def _chain(self):
        base = bytes((i % 251) for i in range(4000))
        v1 = base[:100] + b"A" * 1000 + base[1100:3000] + b"B" * 200 + base[3200:]
        b1 = Backup(
            version="v1", base_version="v0",
            target_length=len(v1), target_digest=diffseg._sha256(v1),
            segments=[Segment("v0", 100, b"A" * 1000),
                      Segment("v0", 3000, b"B" * 200)])
        # v2: 与 v1#0 在 [600,1100) 重叠；把 v1#1 整个 [3000,3200) 吃掉
        v2 = (v1[:600] + b"C" * 900 + v1[1500:2900] + b"D" * 400
              + v1[3300:])
        b2 = Backup(
            version="v2", base_version="v1",
            target_length=len(v2), target_digest=diffseg._sha256(v2),
            segments=[Segment("v1", 600, b"C" * 900),
                      Segment("v1", 2900, b"D" * 400)])
        return base, v1, v2, b1, b2

    def test_latest_wins(self):
        base, v1, v2, b1, b2 = self._chain()
        data, report = restore(base, [b1, b2], base_version="v0")
        self.assertEqual(data, v2)  # 逐字节一致
        self.assertEqual(report.covered_segments, ["v1#1"])
        ov = {(o.covered_segment, o.covering_segment, o.offset, o.length)
              for o in report.overlaps}
        self.assertIn(("v1#0", "v2#0", 600, 500), ov)
        self.assertIn(("v1#1", "v2#1", 3000, 200), ov)
        # 旧链不经过 v2 时仍恢复 v1
        self.assertEqual(restore(base, [b1], base_version="v0")[0], v1)

    def test_overlap_data_files(self):
        with open(os.path.join(OVERLAP_DIR, "base_v0.bin"), "rb") as fh:
            base = fh.read()
        b1 = diffseg.load_backup(os.path.join(OVERLAP_DIR, "backup_v1.json"))
        b2 = diffseg.load_backup(os.path.join(OVERLAP_DIR, "backup_v2.json"))
        with open(os.path.join(OVERLAP_DIR, "expected_v2.bin"), "rb") as fh:
            expected = fh.read()
        with open(os.path.join(OVERLAP_DIR, "expected_report.json"),
                  encoding="utf-8") as fh:
            expected_report = json.load(fh)
        data, report = restore(base, [b1, b2], base_version="v0")
        self.assertEqual(data, expected)
        self.assertEqual(diffseg.report_to_dict(report), expected_report)


class ValidationTests(unittest.TestCase):
    def test_segments_must_not_overlap_within_backup(self):
        bad = Backup(version="v1", base_version="v0", target_length=100,
                     target_digest=diffseg._sha256(b"x" * 100),
                     segments=[Segment("v0", 0, b"x" * 10),
                               Segment("v0", 5, b"y" * 10)])
        with self.assertRaises(ValidationError):
            bad.validate()

    def test_digest_tamper_detected(self):
        backup = encode(b"abcdef", b"abXYef", "v0", "v1", block_size=2)
        d = diffseg.backup_to_dict(backup)
        d["segments"][0]["data_b64"] = "AAAA"  # 内容被改，摘要对不上
        with self.assertRaises(ValidationError):
            diffseg.backup_from_dict(d)
        # 目标摘要被改：恢复后必被发现
        d = diffseg.backup_to_dict(backup)
        d["target_digest"] = "0" * 64
        tampered = diffseg.backup_from_dict(d)
        with self.assertRaises(DigestMismatchError):
            restore(b"abcdef", [tampered])

    def test_missing_base_content(self):
        backup = encode(b"abcdef", b"abXYef", "v0", "v1", block_size=2)
        with self.assertRaises(MissingBaseError):
            restore(None, [backup])

    def test_broken_chain(self):
        b1 = encode(b"abc", b"abC", "v0", "v1", block_size=2)
        b2 = encode(b"abC", b"XbC", "vX", "v2", block_size=2)  # 基准版本对不上
        with self.assertRaises(ChainError):
            restore(b"abc", [b1, b2])
        with self.assertRaises(ChainError):
            restore(b"abc", [b1], base_version="v9")

    def test_segment_base_version_mismatch(self):
        bad = Backup(version="v1", base_version="v0", target_length=4,
                     target_digest=diffseg._sha256(b"abcd"),
                     segments=[Segment("v7", 0, b"ab")])
        with self.assertRaises(ValidationError):
            bad.validate()


class RandomRoundTripTests(unittest.TestCase):
    def test_random_round_trip(self):
        rng = random.Random(1234)
        for _ in range(30):
            base = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 5000)))
            versions = [base]
            backups = []
            for step in range(rng.randrange(1, 4)):
                cur = bytearray(versions[-1])
                for _ in range(rng.randrange(1, 5)):
                    op = rng.choice(("mod", "ins", "del", "trunc", "ext"))
                    if op == "mod" and cur:
                        pos = rng.randrange(len(cur))
                        ln = rng.randrange(1, 50)
                        cur[pos:pos + ln] = bytes(rng.randrange(256)
                                                  for _ in range(rng.randrange(1, 50)))
                    elif op == "ins":
                        pos = rng.randrange(len(cur) + 1)
                        cur[pos:pos] = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
                    elif op == "del" and cur:
                        pos = rng.randrange(len(cur))
                        del cur[pos:pos + rng.randrange(1, 80)]
                    elif op == "trunc":
                        cur = cur[:rng.randrange(0, len(cur) + 1)]
                    else:
                        cur.extend(bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80))))
                nxt = bytes(cur)
                backups.append(encode(versions[-1], nxt, f"v{step}", f"v{step + 1}",
                                      block_size=rng.choice((16, 64, 256, 4096))))
                versions.append(nxt)
            data, _ = restore(base, backups, base_version="v0")
            self.assertEqual(data, versions[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
