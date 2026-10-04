"""LSMTree 自测：python3 -m unittest test_lsm -v"""

import json
import os
import shutil
import tempfile
import unittest

from lsm import LSMTree, TOMBSTONE


def make_db(tmp, **overrides):
    params = dict(
        memtable_max_entries=4,
        num_levels=3,
        level0_capacity_bytes=256,
        fanout=4,
    )
    params.update(overrides)
    return LSMTree(tmp, **params)


def run_records(db, level):
    """读出某层所有 run 的 [(key, value)]，按 run 顺序拼接。"""
    out = []
    for run in db.levels[level]:
        out.extend(run.entries())
    return out


class SingleLevelTest(unittest.TestCase):
    """num_levels=1：只有 L0，不触发层间合并。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_flush_only_no_compaction(self):
        db = make_db(self.tmp, num_levels=1, memtable_max_entries=2)
        for i in range(6):
            db.put("k%02d" % i, "v%d" % i)
        db.close()
        self.assertEqual(len(db.levels[0]), 3)  # 3 次刷盘，3 个 run
        for i in range(6):
            self.assertEqual(db.get("k%02d" % i), "v%d" % i)

    def test_unflushed_memtable_readable(self):
        db = make_db(self.tmp, num_levels=1)
        db.put("a", "1")
        self.assertEqual(db.get("a"), "1")
        self.assertEqual(len(db.levels[0]), 0)  # 未满阈值，未刷盘


class MultiLevelMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_cascading_merge_into_deeper_levels(self):
        # L0 上限 256B、fanout=4 -> L1 上限 1KiB、L2 为最后一层
        db = make_db(self.tmp)
        for i in range(200):
            db.put("key%04d" % i, "val-%04d" % i)
        db.close()
        # 数据应被逐级合并，最终大部分落在最后一层
        self.assertGreater(db.level_size(2), 0)
        for i in range(200):
            self.assertEqual(db.get("key%04d" % i), "val-%04d" % i)

    def test_merge_produces_single_sorted_run_per_deep_level(self):
        db = make_db(self.tmp)
        for i in range(120):
            db.put("k%04d" % i, "v%d" % i)
        db.close()
        for level in (1, 2):
            keys = [k for k, _ in run_records(db, level)]
            self.assertEqual(keys, sorted(keys))
            self.assertEqual(len(keys), len(set(keys)))  # 合并后层内无重复键

    def test_level_capacity_formula(self):
        db = make_db(self.tmp, level0_capacity_bytes=100, fanout=10, num_levels=3)
        self.assertEqual(db.level_capacity(0), 100)
        self.assertEqual(db.level_capacity(1), 1000)
        self.assertEqual(db.level_capacity(2), 10000)


class ShadowingTest(unittest.TestCase):
    """同一键出现在多处时以最新写入为准。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_memtable_shadows_disk(self):
        db = make_db(self.tmp)
        db.put("k", "old")
        db.flush()
        db.put("k", "new")  # 还在内存
        self.assertEqual(db.get("k"), "new")

    def test_newer_run_shadows_older_run_same_level(self):
        db = make_db(self.tmp, num_levels=1, memtable_max_entries=1)
        db.put("k", "v1")
        db.put("k", "v2")  # 触发上一次刷盘后再刷盘
        db.close()
        self.assertEqual(len(db.levels[0]), 2)
        self.assertEqual(db.get("k"), "v2")

    def test_upper_level_shadows_deeper_level(self):
        db = make_db(self.tmp)
        db.put("k", "deep")
        db.flush()
        # 把 "deep" 压进更深层
        for i in range(60):
            db.put("filler%03d" % i, "x" * 8)
        db.close()
        deep_level = max(
            i for i in range(db.num_levels)
            if any(k == "k" for k, _ in run_records(db, i))
        )
        db.put("k", "shallow")
        db.flush()
        self.assertEqual(db.get("k"), "shallow")
        # 旧值仍躺在深层，但被浅层遮蔽
        self.assertTrue(
            any(k == "k" for k, _ in run_records(db, deep_level))
        )

    def test_compaction_resolves_shadowing(self):
        db = make_db(self.tmp)
        db.put("k", "v1")
        db.flush()
        db.put("k", "v2")
        db.flush()
        for i in range(80):  # 填满各层，触发级联合并到最后一层
            db.put("f%03d" % i, "y" * 8)
        db.close()
        last = db.num_levels - 1
        values = [v for k, v in run_records(db, last) if k == "k"]
        self.assertEqual(values, ["v2"])  # 合并后深层只保留最新值
        self.assertEqual(db.get("k"), "v2")


class TombstoneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_delete_in_memtable(self):
        db = make_db(self.tmp)
        db.put("k", "v")
        db.delete("k")
        self.assertIsNone(db.get("k"))

    def test_tombstone_shadows_older_disk_value(self):
        db = make_db(self.tmp)
        db.put("k", "v")
        db.flush()
        db.delete("k")
        db.flush()
        self.assertIsNone(db.get("k"))

    def test_tombstone_survives_intermediate_merge(self):
        db = make_db(self.tmp)
        db.put("k", "v")
        db.flush()
        db.delete("k")
        db.flush()
        db._merge_levels(0, 1)  # 手动合并到中间层
        self.assertIsNone(db.get("k"))
        # 中间层必须保留 tombstone，否则深层旧值会“复活”
        self.assertIn(("k", TOMBSTONE), run_records(db, 1))

    def test_tombstone_dropped_at_last_level(self):
        db = make_db(self.tmp)
        db.put("k", "v")
        db.flush()
        db.delete("k")
        db.flush()
        for i in range(80):  # 触发级联合并直到最后一层
            db.put("f%03d" % i, "y" * 8)
        db.close()
        last = db.num_levels - 1
        self.assertNotIn("k", [k for k, _ in run_records(db, last)])
        self.assertIsNone(db.get("k"))

    def test_delete_then_reput(self):
        db = make_db(self.tmp)
        db.put("k", "v1")
        db.flush()
        db.delete("k")
        db.flush()
        db.put("k", "v2")
        self.assertEqual(db.get("k"), "v2")
        db.flush()
        self.assertEqual(db.get("k"), "v2")

    def test_delete_nonexistent_key(self):
        db = make_db(self.tmp)
        db.delete("ghost")
        db.flush()
        self.assertIsNone(db.get("ghost"))


class DuplicateKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_many_duplicate_keys(self):
        db = make_db(self.tmp, memtable_max_entries=8)
        for i in range(1000):
            db.put("hot", "v%d" % i)
        db.close()
        self.assertEqual(db.get("hot"), "v999")
        # 触发完整合并后，磁盘上该键只剩一条记录
        for i in range(80):
            db.put("f%03d" % i, "y" * 8)
        db.close()
        last = db.num_levels - 1
        occurrences = sum(
            1 for lvl in range(db.num_levels)
            for k, _ in run_records(db, lvl) if k == "hot"
        )
        self.assertEqual(occurrences, 1)
        self.assertEqual(db.get("hot"), "v999")

    def test_duplicate_keys_reduce_disk_footprint(self):
        db = make_db(self.tmp)
        for i in range(500):
            db.put("dup", "payload-%d" % i)
        db.close()
        for i in range(80):
            db.put("f%03d" % i, "y" * 8)
        db.close()
        last = db.num_levels - 1
        dup_records = [v for k, v in run_records(db, last) if k == "dup"]
        self.assertEqual(dup_records, ["payload-499"])


class EdgeCaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_get_missing_key(self):
        db = make_db(self.tmp)
        db.put("a", "1")
        self.assertIsNone(db.get("nope"))

    def test_empty_db_get(self):
        db = make_db(self.tmp)
        self.assertIsNone(db.get("anything"))

    def test_empty_value_and_unicode(self):
        db = make_db(self.tmp, memtable_max_entries=2)
        db.put("empty", "")
        db.put("中文键", "值🎉")
        db.flush()
        self.assertEqual(db.get("empty"), "")
        self.assertEqual(db.get("中文键"), "值🎉")

    def test_flush_threshold_boundary(self):
        db = make_db(self.tmp, memtable_max_entries=3)
        db.put("a", "1")
        db.put("b", "2")
        self.assertEqual(len(db.levels[0]), 0)  # 未达阈值
        db.put("c", "3")  # 第 3 条，触发刷盘
        self.assertEqual(len(db.levels[0]), 1)
        self.assertEqual(len(db.memtable), 0)

    def test_same_key_counts_once_toward_threshold(self):
        db = make_db(self.tmp, memtable_max_entries=2)
        db.put("k", "v1")
        db.put("k", "v2")
        db.put("k", "v3")  # 同一键反复写，不触发刷盘
        self.assertEqual(len(db.levels[0]), 0)
        self.assertEqual(db.get("k"), "v3")

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            LSMTree(self.tmp, memtable_max_entries=0)
        with self.assertRaises(ValueError):
            LSMTree(self.tmp, num_levels=0)
        with self.assertRaises(ValueError):
            LSMTree(self.tmp, fanout=0)

    def test_put_none_value_rejected(self):
        db = make_db(self.tmp)
        with self.assertRaises(ValueError):
            db.put("k", None)

    def test_reopen_recovers_data(self):
        db = make_db(self.tmp)
        for i in range(50):
            db.put("k%03d" % i, "v%d" % i)
        db.delete("k000")
        db.close()
        db2 = make_db(self.tmp)  # 同目录重开
        self.assertIsNone(db2.get("k000"))
        self.assertEqual(db2.get("k001"), "v1")
        self.assertEqual(db2.get("k049"), "v49")

    def test_run_files_are_sorted_jsonl(self):
        db = make_db(self.tmp, memtable_max_entries=2)
        db.put("b", "2")
        db.put("a", "1")
        db.flush()
        run = db.levels[0][0]
        with open(run.path, encoding="utf-8") as f:
            keys = [json.loads(line)[0] for line in f]
        self.assertEqual(keys, ["a", "b"])


class WriteAmplificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_wa_accounting(self):
        db = make_db(self.tmp)
        for i in range(200):
            db.put("key%04d" % i, "val-%04d" % i)
        db.close()
        stats = db.stats()
        self.assertGreater(stats["disk_bytes"], 0)
        self.assertGreater(stats["user_bytes"], 0)
        self.assertGreaterEqual(stats["write_amplification"], 1.0)
        # 每层写盘字节之和 == 总写盘字节
        self.assertEqual(
            sum(stats["level_bytes_written"]), stats["disk_bytes"]
        )

    def test_no_flush_no_disk_write(self):
        db = make_db(self.tmp)
        db.put("k", "v")
        self.assertEqual(db.stats()["disk_bytes"], 0)
        self.assertEqual(db.stats()["write_amplification"], 0.0)


if __name__ == "__main__":
    unittest.main()
