"""pagestore 自测：损坏定位、多数表决、写新页修复、边界用例。

运行：python3 -m unittest -v
"""
from __future__ import annotations

import os
import tempfile
import unittest

from pagestore import (
    CRC_OFFSET,
    HEADER_SIZE,
    MAGIC_OFFSET,
    VERSION_OFFSET,
    Decision,
    MultiReplicaStore,
    ReplicaStatus,
    ReplicaStore,
)


def make_store(tmpdir, n=3):
    paths = [os.path.join(tmpdir, f"replica{i}.log") for i in range(n)]
    stores = [ReplicaStore(p) for p in paths]
    return MultiReplicaStore(stores), stores, paths


def sample_data(seed=0, size=512):
    return bytes(((i * 7 + seed) % 256) for i in range(size))


class PageStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store, self.replicas, self.paths = make_store(self.tmp.name, 3)
        self.data = sample_data()
        self.store.write_page(1, self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def sizes(self):
        return [os.path.getsize(p) for p in self.paths]

    # ---- 正常路径 -------------------------------------------------------

    def test_roundtrip_and_normal_read(self):
        report = self.store.read_page(1)
        self.assertTrue(report.healthy)
        self.assertEqual(report.consensus, self.data)
        self.assertEqual([d.status for d in report.replicas], [ReplicaStatus.OK] * 3)
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.NO_REPAIR_NEEDED)
        self.assertEqual(self.sizes().count(self.sizes()[0]), 3)

    def test_version_monotonic(self):
        v1 = self.store.write_page(1, sample_data(1))
        v2 = self.store.write_page(1, sample_data(2))
        self.assertEqual((v1, v2), (2, 3))
        for r in self.replicas:
            self.assertEqual(r.read_latest(1).version, 3)

    # ---- 场景 1：单副本损坏 ---------------------------------------------

    def test_single_replica_data_corruption_located_and_repaired(self):
        byte_offset_in_page = HEADER_SIZE + 10
        self.replicas[0].tamper(1, byte_offset_in_page)

        report = self.store.read_page(1)
        self.assertEqual(report.page_no, 1)
        self.assertEqual(report.consensus, self.data)
        diag0 = report.replicas[0]
        self.assertEqual(diag0.status, ReplicaStatus.DATA_CORRUPT)
        self.assertIn(("data", byte_offset_in_page, 1),
                      [(c.region, c.offset, c.length) for c in diag0.corruptions])
        self.assertEqual([d.status for d in report.replicas[1:]],
                         [ReplicaStatus.OK, ReplicaStatus.OK])

        sizes_before = self.sizes()
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.REPAIRED)

        change0 = repair.changes[0]
        self.assertEqual(change0.action, "repaired")
        self.assertEqual((change0.version_before, change0.version_after), (1, 2))
        self.assertNotEqual(change0.crc_before, change0.crc_after)
        self.assertEqual([c.action for c in repair.changes[1:]], ["keep", "keep"])

        # 修复后三副本全部健康，且新页校验值与版本正确
        after = self.store.read_page(1)
        self.assertTrue(after.healthy)
        self.assertEqual([d.status for d in after.replicas], [ReplicaStatus.OK] * 3)
        self.assertEqual(self.replicas[0].read_latest(1).stored_crc,
                         change0.crc_after)

        # 只追加新页：文件增长，旧坏页字节仍留在磁盘上，没有原地覆盖
        sizes_after = self.sizes()
        self.assertGreater(sizes_after[0], sizes_before[0])
        self.assertEqual(sizes_after[1:], sizes_before[1:])
        with open(self.paths[0], "rb") as fh:
            raw = fh.read()
        # 第一条记录（4 字节长度头之后）中被翻转的坏字节依然存在
        self.assertEqual(raw[4 + byte_offset_in_page], self.data[10] ^ 0xFF)

    def test_multi_byte_corruption_returns_ranges(self):
        self.replicas[1].tamper(1, HEADER_SIZE + 5)
        self.replicas[1].tamper(1, HEADER_SIZE + 6)
        self.replicas[1].tamper(1, HEADER_SIZE + 100)
        diag = self.store.read_page(1).replicas[1]
        self.assertEqual(diag.status, ReplicaStatus.DATA_CORRUPT)
        ranges = {(c.offset, c.length) for c in diag.corruptions}
        self.assertEqual(ranges, {(HEADER_SIZE + 5, 2), (HEADER_SIZE + 100, 1)})

    # ---- 场景 2：多数副本损坏 -> 放弃，好数据不被覆盖 --------------------

    def test_majority_corrupt_aborts_and_preserves_good_copy(self):
        self.replicas[0].tamper(1, HEADER_SIZE + 3)
        self.replicas[1].tamper(1, HEADER_SIZE + 9)
        sizes_before = self.sizes()

        report = self.store.read_page(1)
        self.assertFalse(report.healthy)  # 健康副本 1/3，不过半
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.ABORT_NO_MAJORITY)
        self.assertEqual(repair.changes, [])

        # 任何副本都不被写入，好副本数据原样保留
        self.assertEqual(self.sizes(), sizes_before)
        self.assertEqual(self.replicas[2].read_latest(1).data, self.data)

    def test_majority_identically_corrupt_still_aborts(self):
        # 坏得一模一样也没用：它们校验不自洽，不参与投票
        for r in self.replicas[:2]:
            r.tamper(1, HEADER_SIZE + 3)
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.ABORT_NO_MAJORITY)
        self.assertEqual(self.replicas[2].read_latest(1).data, self.data)

    # ---- 场景 3：全副本损坏 ---------------------------------------------

    def test_all_replicas_corrupt_aborts(self):
        for i, r in enumerate(self.replicas):
            r.tamper(1, HEADER_SIZE + i)
        report = self.store.read_page(1)
        # 没有任何健康参照时只能报“校验失败、无法定位”，不能瞎猜位置
        self.assertTrue(all(d.status == ReplicaStatus.UNVERIFIABLE
                            for d in report.replicas))
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.ABORT_NO_HEALTHY_SOURCE)
        self.assertEqual(repair.changes, [])

    # ---- 场景 4：校验值本身存疑（数据完好）------------------------------

    def test_crc_field_suspect_located_and_repaired(self):
        self.replicas[1].tamper(1, CRC_OFFSET)
        report = self.store.read_page(1)
        diag = report.replicas[1]
        self.assertEqual(diag.status, ReplicaStatus.CRC_SUSPECT)
        self.assertEqual([(c.region, c.offset, c.length) for c in diag.corruptions],
                         [("header:crc", CRC_OFFSET, 4)])
        self.assertFalse(diag.stored_crc == diag.computed_crc)

        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.REPAIRED)
        change = repair.changes[1]
        self.assertEqual(change.action, "repaired")
        self.assertEqual(change.version_before, 1)
        self.assertEqual(change.version_after, 2)
        self.assertEqual(change.crc_after, self.replicas[1].read_latest(1).stored_crc)
        # 数据内容一字节不变，只是重写带正确校验值的新页
        self.assertEqual(self.replicas[1].read_latest(1).data, self.data)
        self.assertTrue(self.store.read_page(1).healthy)

    def test_version_field_suspect_located(self):
        self.replicas[0].tamper(1, VERSION_OFFSET)
        diag = self.store.read_page(1).replicas[0]
        self.assertEqual(diag.status, ReplicaStatus.VERSION_SUSPECT)
        self.assertEqual(diag.corruptions[0].region, "header:version")
        self.assertEqual(self.store.repair_page(1).decision, Decision.REPAIRED)

    # ---- 票数相同：放弃 --------------------------------------------------

    def test_tie_vote_aborts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store4, replicas4, paths4 = make_store(tmp.name, 4)
        data_a, data_b = sample_data(10), sample_data(20)
        replicas4[0].append_page(2, 1, data_a)
        replicas4[1].append_page(2, 1, data_a)
        replicas4[2].append_page(2, 1, data_b)
        replicas4[3].append_page(2, 1, data_b)

        sizes_before = [os.path.getsize(p) for p in paths4]
        repair = store4.repair_page(2)
        self.assertEqual(repair.decision, Decision.ABORT_NO_MAJORITY)
        self.assertIn("票数相同", repair.reason)
        self.assertEqual([os.path.getsize(p) for p in paths4], sizes_before)

    def test_three_way_split_aborts(self):
        # 3 个校验自洽但数据各不相同 -> 1/1/1，无过半
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store3, replicas3, _ = make_store(tmp.name, 3)
        for i, seed in enumerate((10, 20, 30)):
            replicas3[i].append_page(3, 1, sample_data(seed))
        self.assertEqual(store3.repair_page(3).decision, Decision.ABORT_NO_MAJORITY)

    # ---- 其他边界用例 ----------------------------------------------------

    def test_missing_replica_filled_from_majority(self):
        os.remove(self.paths[2])
        report = self.store.read_page(1)
        self.assertEqual(report.replicas[2].status, ReplicaStatus.MISSING)
        self.assertTrue(report.healthy)  # 2/3 过半
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.REPAIRED)
        change = repair.changes[2]
        self.assertEqual(change.action, "repaired")
        self.assertIsNone(change.version_before)
        self.assertEqual(change.version_after, 2)
        self.assertTrue(self.store.read_page(1).healthy)

    def test_stale_but_valid_replica_caught_up(self):
        # 副本 2 校验自洽但持有旧版本数据（如掉线后恢复）
        old_data = sample_data(99)
        self.replicas[2].append_page(1, 2, old_data)
        diag = self.store.read_page(1).replicas[2]
        self.assertEqual(diag.status, ReplicaStatus.STALE)
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.REPAIRED)
        self.assertEqual(repair.changes[2].action, "repaired")
        self.assertEqual(self.replicas[2].read_latest(1).data, self.data)

    def test_header_magic_corruption(self):
        self.replicas[0].tamper(1, MAGIC_OFFSET)
        diag = self.store.read_page(1).replicas[0]
        self.assertEqual(diag.status, ReplicaStatus.HEADER_CORRUPT)
        self.assertEqual(diag.corruptions[0].region, "header:magic")
        repair = self.store.repair_page(1)
        self.assertEqual(repair.decision, Decision.REPAIRED)
        self.assertTrue(self.store.read_page(1).healthy)

    def test_repeated_repair_is_idempotent(self):
        self.replicas[0].tamper(1, HEADER_SIZE + 1)
        self.assertEqual(self.store.repair_page(1).decision, Decision.REPAIRED)
        again = self.store.repair_page(1)
        self.assertEqual(again.decision, Decision.NO_REPAIR_NEEDED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
