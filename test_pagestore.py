"""pagestore 自测：校验定位、多数决修复、版本变化与边界用例。

运行：python3 -m unittest test_pagestore -v
"""
import os
import tempfile
import unittest

from pagestore import (HEADER_SIZE, TRAILER_SIZE, Decision, MajorityUnavailableError,
                       PageNotFoundError, PageStore, Region, State)

PAYLOAD = b"the quick brown fox jumps over the lazy dog" * 4


def flip_byte(path, offset):
    with open(path, "r+b") as f:
        f.seek(offset)
        value = f.read(1)[0]
        f.seek(offset)
        f.write(bytes([value ^ 0xFF]))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PageStore(self.tmp.name, replicas=3)
        self.store.write(7, PAYLOAD)

    def path(self, r):
        return self.store._paths[r]

    def corrupt_payload(self, r, page=7, byte=0):
        rec = self.store.locate(page, r)
        flip_byte(self.path(r), rec.offset + HEADER_SIZE + byte)
        return rec

    def corrupt_checksum_copy(self, r, page=7, copy=2):
        rec = self.store.locate(page, r)
        off = rec.offset + HEADER_SIZE + len(rec.payload) + (4 if copy == 2 else 0)
        flip_byte(self.path(r), off)
        return rec, off

    def corrupt_header(self, r, page=7):
        rec = self.store.locate(page, r)
        flip_byte(self.path(r), rec.offset + 6)  # page_no 字段内
        return rec


class TestChecksumLocate(Base):
    def test_payload_corruption_located(self):
        rec = self.corrupt_payload(0, byte=5)
        st = self.store.verify(7)[0]
        self.assertEqual(st.state, State.CORRUPT)
        self.assertEqual(st.region, Region.PAYLOAD)
        self.assertEqual(st.offset, rec.offset + HEADER_SIZE)

    def test_header_corruption_located(self):
        rec = self.corrupt_header(1)
        st = self.store.verify(7)[1]
        self.assertEqual(st.state, State.CORRUPT)
        self.assertEqual(st.region, Region.HEADER)
        self.assertEqual(st.offset, rec.offset)

    def test_suspect_checksum_located(self):
        _, off = self.corrupt_checksum_copy(2, copy=2)
        st = self.store.verify(7)[2]
        self.assertEqual(st.state, State.SUSPECT_CHECKSUM)
        self.assertEqual(st.region, Region.CHECKSUM)
        self.assertEqual(st.offset, off)

    def test_diff_offsets_reported(self):
        self.corrupt_payload(0, byte=3)
        report = self.store.repair(7)
        bad = report.statuses[0]
        self.assertEqual(bad.diff_offsets, [3])


class TestRepairDecision(Base):
    def test_no_op_when_all_good(self):
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.NO_OP)
        self.assertEqual(report.changes, [])

    def test_single_replica_corruption_repaired(self):
        before_size = os.path.getsize(self.path(0))
        rec = self.corrupt_payload(0)
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.REPAIRED)
        self.assertEqual(report.source_replicas, [1, 2])
        self.assertEqual([c.replica for c in report.changes], [0])
        ch = report.changes[0]
        self.assertEqual(ch.before_version, 1)
        self.assertEqual(ch.after_version, 2)          # 版本号 +1
        self.assertNotEqual(ch.before_crc, ch.after_crc)
        self.assertEqual(ch.after_crc, report.statuses[1].computed_crc)
        # 写新页而非原地覆盖：文件变长，旧坏字节仍在原处
        self.assertGreater(os.path.getsize(self.path(0)), before_size)
        with open(self.path(0), "rb") as f:
            f.seek(rec.offset + HEADER_SIZE)
            self.assertNotEqual(f.read(len(PAYLOAD)), PAYLOAD)
        # 修复后三副本一致
        self.assertTrue(all(s.state == State.GOOD for s in self.store.verify(7)))
        self.assertEqual(self.store.read(7), PAYLOAD)

    def test_suspect_checksum_repaired(self):
        self.corrupt_checksum_copy(2, copy=1)
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.REPAIRED)
        self.assertEqual([c.replica for c in report.changes], [2])
        self.assertEqual(report.changes[0].note, "重写存疑校验值")
        self.assertEqual(report.changes[0].before_crc, report.changes[0].after_crc)  # 数据没变
        self.assertTrue(all(s.state == State.GOOD for s in self.store.verify(7)))

    def test_majority_corruption_aborts(self):
        self.corrupt_payload(0)
        self.corrupt_payload(1)
        good_before = open(self.path(2), "rb").read()
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.ABORTED)
        self.assertEqual(report.votes, {report.statuses[2].computed_crc: 1})
        # 好副本绝不被坏数据覆盖
        self.assertEqual(open(self.path(2), "rb").read(), good_before)
        with self.assertRaises(MajorityUnavailableError):
            self.store.read(7)

    def test_all_corruption_aborts(self):
        for r in range(3):
            self.corrupt_payload(r)
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.ABORTED)
        self.assertEqual(report.votes, {})
        self.assertIn("全部副本校验失败", report.reason)

    def test_tie_aborts(self):
        # r0=A(好) r1=B(好但内容不同) r2=坏 → 1:1 平票
        self.store._append(1, 7, 2, b"different but checksummed content")
        self.corrupt_payload(2)
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.ABORTED)
        self.assertEqual(sorted(report.votes.values()), [1, 1])
        self.assertIn("未达法定多数", report.reason)

    def test_tie_aborts_four_replicas(self):
        store = PageStore(os.path.join(self.tmp.name, "four"), replicas=4)
        store.write(1, b"AAAA")
        store._append(2, 1, 2, b"BBBB")
        store._append(3, 1, 2, b"BBBB")
        report = store.repair(1)  # 2:2 平票
        self.assertEqual(report.decision, Decision.ABORTED)
        self.assertEqual(sorted(report.votes.values()), [2, 2])

    def test_repair_after_header_corruption(self):
        self.corrupt_header(0)
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.REPAIRED)
        # 页头损坏处之后能重新同步，新页可读
        self.assertEqual(self.store.read(7), PAYLOAD)
        self.assertTrue(all(s.state == State.GOOD for s in self.store.verify(7)))


class TestEdgeCases(Base):
    def test_empty_payload(self):
        self.store.write(9, b"")
        self.assertEqual(self.store.read(9), b"")
        self.corrupt_payload(0, page=9, byte=0) if False else None
        report = self.store.repair(9)
        self.assertEqual(report.decision, Decision.NO_OP)

    def test_page_not_found(self):
        with self.assertRaises(PageNotFoundError):
            self.store.read(999)

    def test_version_monotonic(self):
        v1 = self.store.write(3, b"a")
        v2 = self.store.write(3, b"b")
        self.assertEqual((v1, v2), (1, 2))
        self.assertEqual(self.store.read(3), b"b")

    def test_missing_replica_page_filled(self):
        store = PageStore(os.path.join(self.tmp.name, "m"), replicas=3)
        store.write(5, b"hello")
        store._append(0, 6, 1, b"in-r0-r1")  # r2 没有第 6 页，r0/r1 构成多数
        store._append(1, 6, 1, b"in-r0-r1")
        report = store.repair(6)
        self.assertEqual(report.decision, Decision.REPAIRED)
        self.assertEqual([c.note for c in report.changes], ["补齐缺失页"])
        self.assertEqual(store.read(6), b"in-r0-r1")

    def test_single_copy_page_not_trusted(self):
        # 只有 1/3 副本持有的页达不到法定多数，不能作为修复来源
        store = PageStore(os.path.join(self.tmp.name, "m1"), replicas=3)
        store.write(5, b"hello")
        store._append(0, 6, 1, b"only-in-r0")
        report = store.repair(6)
        self.assertEqual(report.decision, Decision.ABORTED)

    def test_truncated_page(self):
        size = os.path.getsize(self.path(1))
        os.truncate(self.path(1), size - 4)  # 截掉半个 trailer
        st = self.store.verify(7)[1]
        self.assertEqual(st.state, State.CORRUPT)
        self.assertEqual(st.region, Region.TRUNCATED)
        report = self.store.repair(7)
        self.assertEqual(report.decision, Decision.REPAIRED)
        self.assertEqual(self.store.read(7), PAYLOAD)

    def test_large_payload(self):
        big = bytes(range(256)) * 1024  # 256 KiB
        self.store.write(11, big)
        self.corrupt_payload(2, page=11, byte=1000)
        self.assertEqual(self.store.read(11), big)

    def test_read_without_repair_raises_on_tie(self):
        self.store._append(1, 7, 2, b"other valid content")
        self.corrupt_payload(2)
        with self.assertRaises(MajorityUnavailableError):
            self.store.read(7)


if __name__ == "__main__":
    unittest.main()
