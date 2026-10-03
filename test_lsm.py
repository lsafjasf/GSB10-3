"""test_lsm.py — LSM 引擎自测（标准库 unittest）。

运行：python3 test_lsm.py  或  python3 -m unittest test_lsm -v
"""

import os
import shutil
import tempfile
import unittest

from lsm import LSMEngine, TOMBSTONE, _read_records


class LSMTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lsm-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def make_engine(self, **kw):
        return LSMEngine(self.dir, **kw)

    @staticmethod
    def k(i):
        return ("key%06d" % i).encode()

    @staticmethod
    def v(i):
        return ("val%06d" % i).encode()


class TestBasic(LSMTestCase):
    def test_put_get_memtable_only(self):
        eng = self.make_engine()
        eng.put(b"a", b"1")
        eng.put("b", "2")  # str 输入自动转 bytes
        self.assertEqual(eng.get(b"a"), b"1")
        self.assertEqual(eng.get(b"b"), b"2")
        self.assertIsNone(eng.get(b"missing"))

    def test_flush_at_exact_threshold(self):
        """边界：第 memtable_max_entries 条写入触发刷盘。"""
        eng = self.make_engine(memtable_max_entries=10, l0_compaction_trigger=100)
        for i in range(9):
            eng.put(self.k(i), self.v(i))
        self.assertEqual(eng.flush_count, 0)
        eng.put(self.k(9), self.v(9))
        self.assertEqual(eng.flush_count, 1)
        self.assertEqual(len(eng.level_runs(0)), 1)
        for i in range(10):
            self.assertEqual(eng.get(self.k(i)), self.v(i))

    def test_overwrite_inside_memtable(self):
        eng = self.make_engine()
        eng.put(b"x", b"old")
        eng.put(b"x", b"new")
        self.assertEqual(eng.get(b"x"), b"new")
        self.assertEqual(eng.stats()["logical_bytes"] > 0, True)

    def test_get_empty_engine(self):
        eng = self.make_engine()
        self.assertIsNone(eng.get(b"nope"))
        self.assertEqual(list(eng.scan()), [])


class TestShadowing(LSMTestCase):
    """同一键出现在内存表 / L0 / 更深层时，必须以最新写入为准。"""

    def test_shadow_across_memtable_and_disk(self):
        eng = self.make_engine(memtable_max_entries=4, l0_compaction_trigger=100)
        eng.put(b"dup", b"disk")
        for i in range(3):  # 凑满 4 条触发刷盘
            eng.put(self.k(i), self.v(i))
        self.assertEqual(eng.flush_count, 1)
        eng.put(b"dup", b"mem")  # 新值还在内存表
        self.assertEqual(eng.get(b"dup"), b"mem")

    def test_shadow_across_levels(self):
        """同一键在 L0 与 L1 都有，读取必须取 L0 的较新值。"""
        eng = self.make_engine(memtable_max_entries=2, l0_compaction_trigger=2,
                               max_levels=3, size_ratio=10)
        eng.put(b"k", b"v1")   # 进入第一个 L0 run
        eng.put(b"a", b"1")
        eng.put(b"k", b"v2")   # 第二个 L0 run，触发 L0->L1 合并
        eng.put(b"b", b"2")
        self.assertEqual(eng.get(b"k"), b"v2")
        eng.put(b"k", b"v3")   # 落在新的 L0 run / 内存表
        self.assertEqual(eng.get(b"k"), b"v3")
        eng.flush()
        self.assertEqual(eng.get(b"k"), b"v3")

    def test_shadow_survives_compaction(self):
        """合并后旧版本必须被丢弃，磁盘上每个键每层只保留最新记录。"""
        eng = self.make_engine(memtable_max_entries=2, l0_compaction_trigger=2,
                               max_levels=3, size_ratio=10)
        for round_no in range(5):
            eng.put(b"hot", ("v%d" % round_no).encode())
            eng.put(self.k(round_no), self.v(round_no))
        eng.flush()
        self.assertEqual(eng.get(b"hot"), b"v4")
        # 合并到最后一层后，磁盘上 hot 的记录应只剩一条
        seen = 0
        for level in range(1, eng.max_levels):
            for run in eng.level_runs(level):
                for key, _value, _seq in _read_records(run.path):
                    if key == b"hot":
                        seen += 1
        self.assertEqual(seen, 1)

    def test_scan_returns_shadowed_view(self):
        eng = self.make_engine(memtable_max_entries=3, l0_compaction_trigger=2)
        eng.put(b"a", b"1")
        eng.put(b"b", b"1")
        eng.put(b"a", b"2")
        eng.put(b"c", b"1")
        eng.put(b"b", b"2")
        eng.flush()
        self.assertEqual(list(eng.scan()),
                         [(b"a", b"2"), (b"b", b"2"), (b"c", b"1")])


class TestDelete(LSMTestCase):
    def test_delete_in_memtable(self):
        eng = self.make_engine()
        eng.put(b"x", b"1")
        eng.delete(b"x")
        self.assertIsNone(eng.get(b"x"))

    def test_delete_shadows_disk_value(self):
        """墓碑在内存表/L0，旧值在更深层：读取必须返回已删除。"""
        eng = self.make_engine(memtable_max_entries=2, l0_compaction_trigger=2,
                               max_levels=3)
        eng.put(b"victim", b"old")
        eng.put(b"filler1", b"1")
        eng.put(b"filler2", b"2")
        eng.put(b"filler3", b"3")  # victim 已合并到 L1
        self.assertEqual(eng.get(b"victim"), b"old")
        eng.delete(b"victim")
        self.assertIsNone(eng.get(b"victim"))
        eng.flush()
        self.assertIsNone(eng.get(b"victim"))

    def test_tombstone_kept_above_last_level(self):
        """墓碑合并到非最后一层时必须保留（下层可能还有旧值）。"""
        eng = self.make_engine(memtable_max_entries=2, l0_compaction_trigger=2,
                               max_levels=4, size_ratio=2, level_capacity_base=2)
        # 把旧值压到深层：L1 容量=2*2=4 条，写满后触发 L1->L2
        eng.put(b"victim", b"old")
        for i in range(1, 8):
            eng.put(self.k(i), self.v(i))
        eng.flush()
        # 删除 victim，并再写两轮让墓碑经 L0->L1 合并进入中间层
        eng.delete(b"victim")
        eng.put(b"zzzz", b"pad")   # 刷盘，L0 出现含墓碑的 run
        eng.put(b"yyyy", b"pad")
        eng.put(b"xxxx", b"pad")   # 再次刷盘，触发 L0->L1 合并
        eng.flush()
        # 安全不变式：只要磁盘上还存在 victim 的旧值，
        # 磁盘上就必须同时存在 victim 的墓碑（否则旧值会复活）
        old_on_disk = False
        tombstone_on_disk = 0
        for level in range(eng.max_levels):
            for run in eng.level_runs(level):
                for key, value, _seq in _read_records(run.path):
                    if key != b"victim":
                        continue
                    if value is None:
                        tombstone_on_disk += 1
                    else:
                        old_on_disk = True
        if old_on_disk:
            self.assertGreaterEqual(tombstone_on_disk, 1)
        self.assertIsNone(eng.get(b"victim"))

    def test_tombstone_dropped_at_last_level(self):
        """合并进最后一层后墓碑被真正清理，磁盘上不再存在该键。"""
        eng = self.make_engine(memtable_max_entries=2, l0_compaction_trigger=2,
                               max_levels=2, size_ratio=10)
        eng.put(b"gone", b"1")
        eng.put(b"f", b"1")
        eng.delete(b"gone")
        eng.put(b"g", b"2")  # 触发 L0->L1（最后一层）合并
        eng.flush()
        self.assertIsNone(eng.get(b"gone"))
        for run in eng.level_runs(1):
            for key, _value, _seq in _read_records(run.path):
                self.assertNotEqual(key, b"gone")

    def test_delete_then_reput(self):
        eng = self.make_engine(memtable_max_entries=2, l0_compaction_trigger=2,
                               max_levels=3)
        eng.put(b"k", b"v1")
        eng.put(b"p", b"1")
        eng.delete(b"k")
        eng.put(b"q", b"2")
        self.assertIsNone(eng.get(b"k"))
        eng.put(b"k", b"v2")
        self.assertEqual(eng.get(b"k"), b"v2")
        eng.flush()
        self.assertEqual(eng.get(b"k"), b"v2")

    def test_delete_nonexistent_key(self):
        eng = self.make_engine()
        eng.delete(b"ghost")  # 不存在的键删除不应报错
        self.assertIsNone(eng.get(b"ghost"))


class TestMultiLevel(LSMTestCase):
    def test_multi_level_merge_correctness(self):
        """多层合并后随机校验一批键的值。"""
        eng = self.make_engine(memtable_max_entries=50, l0_compaction_trigger=4,
                               max_levels=4, size_ratio=4, level_capacity_base=50)
        expected = {}
        for i in range(3000):
            key = self.k(i % 700)  # 制造重复键
            value = ("v%08d" % i).encode()
            eng.put(key, value)
            expected[key] = value
        eng.flush()
        self.assertGreater(eng.compaction_count, 0)
        for key, value in expected.items():
            self.assertEqual(eng.get(key), value)
        # L1..L(max-2) 均不得超过容量上限（最后一层只做输入，无上限）
        for level in range(1, eng.max_levels - 1):
            cap = eng.level_capacity_base * (eng.size_ratio ** level)
            self.assertLessEqual(eng.level_entries(level), cap + eng.target_run_entries)

    def test_level1_runs_are_disjoint_and_sorted(self):
        """L1+ 不变式：run 按键排序且键区间互不重叠。"""
        eng = self.make_engine(memtable_max_entries=10, l0_compaction_trigger=3,
                               max_levels=4, size_ratio=4, level_capacity_base=10)
        for i in range(500):
            eng.put(self.k(i), self.v(i))
        eng.flush()
        for level in range(1, eng.max_levels):
            runs = eng.level_runs(level)
            for prev, cur in zip(runs, runs[1:]):
                self.assertLess(prev.max_key, cur.min_key)

    def test_single_level_l0_only(self):
        """单层情形：max_levels=2 且数据量小，只有 L0 与内存表。"""
        eng = self.make_engine(memtable_max_entries=10, l0_compaction_trigger=100,
                               max_levels=2)
        for i in range(25):
            eng.put(self.k(i), self.v(i))
        eng.flush()
        self.assertEqual(eng.compaction_count, 0)
        self.assertEqual(len(eng.level_runs(0)), 3)
        for i in range(25):
            self.assertEqual(eng.get(self.k(i)), self.v(i))

    def test_two_level_merge(self):
        eng = self.make_engine(memtable_max_entries=10, l0_compaction_trigger=2,
                               max_levels=2)
        for i in range(40):
            eng.put(self.k(i), self.v(i))
        eng.flush()
        self.assertGreater(eng.compaction_count, 0)
        self.assertGreater(eng.level_entries(1), 0)
        for i in range(40):
            self.assertEqual(eng.get(self.k(i)), self.v(i))


class TestDuplicateKeys(LSMTestCase):
    def test_heavy_duplicate_keys(self):
        """大量重复键：10 万条逻辑写只有 100 个不同键。"""
        eng = self.make_engine(memtable_max_entries=500, l0_compaction_trigger=4,
                               max_levels=4, size_ratio=10)
        rounds = 1000
        for r in range(rounds):
            for i in range(100):
                eng.put(self.k(i), ("r%06d" % r).encode())
        eng.flush()
        for i in range(100):
            self.assertEqual(eng.get(self.k(i)), ("r%06d" % (rounds - 1)).encode())
        # 重复键应在合并中被压缩：磁盘总条目数远小于逻辑写入条数
        disk_entries = sum(eng.level_entries(l) for l in range(eng.max_levels))
        self.assertLess(disk_entries, rounds * 100 // 10)

    def test_duplicate_keys_with_deletes(self):
        eng = self.make_engine(memtable_max_entries=100, l0_compaction_trigger=4,
                               max_levels=3)
        for r in range(50):
            for i in range(20):
                if r % 3 == 2:
                    eng.delete(self.k(i))
                else:
                    eng.put(self.k(i), ("r%d" % r).encode())
        eng.flush()
        for i in range(20):
            # 最后一轮 r=49，49 % 3 == 1，应为 put
            self.assertEqual(eng.get(self.k(i)), b"r49")


class TestWriteAmplification(LSMTestCase):
    def test_wa_metric_sanity(self):
        """写放大 >= 1，且刷盘本身即贡献物理写。"""
        eng = self.make_engine(memtable_max_entries=100, l0_compaction_trigger=4,
                               max_levels=3)
        for i in range(1000):
            eng.put(self.k(i), self.v(i))
        eng.flush()
        stats = eng.stats()
        self.assertGreaterEqual(stats["write_amplification"], 1.0)
        self.assertGreater(stats["physical_bytes"], 0)
        self.assertGreater(stats["compaction_count"], 0)

    def test_more_levels_not_less_wa_for_same_data(self):
        """同等工作负载下，层数更多时物理写字节数应不少于少层配置（趋势性校验）。"""
        physical = []
        for levels in (2, 4):
            shutil.rmtree(self.dir, True)
            os.makedirs(self.dir)
            eng = self.make_engine(memtable_max_entries=200,
                                   l0_compaction_trigger=4,
                                   max_levels=levels, size_ratio=10)
            for i in range(20000):
                eng.put(self.k(i), self.v(i))
            eng.flush()
            physical.append(eng.stats()["physical_bytes"])
        self.assertLessEqual(physical[0], physical[1])


class TestBoundary(LSMTestCase):
    def test_empty_flush_is_noop(self):
        eng = self.make_engine()
        eng.flush()
        self.assertEqual(eng.flush_count, 0)

    def test_empty_value_and_key(self):
        eng = self.make_engine(memtable_max_entries=2)
        eng.put(b"", b"")          # 空 key 空 value
        eng.put(b"k", b"")         # 空 value（与删除区分）
        eng.flush()
        self.assertEqual(eng.get(b""), b"")
        self.assertEqual(eng.get(b"k"), b"")
        self.assertIsNone(eng.get(b"absent"))

    def test_binary_keys_and_values(self):
        eng = self.make_engine(memtable_max_entries=2)
        eng.put(b"\x00\xff\x01", b"\x00\x01\x02\xff")
        eng.put(b"\x00\xff\x02", b"\xfe")
        eng.flush()
        self.assertEqual(eng.get(b"\x00\xff\x01"), b"\x00\x01\x02\xff")
        self.assertEqual(eng.get(b"\x00\xff\x02"), b"\xfe")

    def test_large_value(self):
        eng = self.make_engine(memtable_max_entries=2)
        blob = os.urandom(100_000)
        eng.put(b"big", blob)
        eng.put(b"small", b"s")
        eng.flush()
        self.assertEqual(eng.get(b"big"), blob)

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            self.make_engine(max_levels=1)
        with self.assertRaises(ValueError):
            self.make_engine(l0_compaction_trigger=1)

    def test_interleaved_random_workload(self):
        """随机混合 put/delete，与字典模型逐键对拍。"""
        import random
        rng = random.Random(42)
        eng = self.make_engine(memtable_max_entries=17, l0_compaction_trigger=3,
                               max_levels=4, size_ratio=3, level_capacity_base=17)
        model = {}
        for _ in range(5000):
            key = self.k(rng.randrange(300))
            if rng.random() < 0.25:
                eng.delete(key)
                model.pop(key, None)
            else:
                value = ("v%d" % rng.randrange(10**6)).encode()
                eng.put(key, value)
                model[key] = value
        eng.flush()
        for i in range(300):
            self.assertEqual(eng.get(self.k(i)), model.get(self.k(i)))
        self.assertEqual(list(eng.scan()), sorted(model.items()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
