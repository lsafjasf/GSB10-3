"""回归测试：首次导出 / 无变化 / 大量变化 / 位点丢失 / 崩溃恢复 / 对拍等价。

运行：python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from export_tool import checkpoint as ckpt_mod
from export_tool import exporter, reconcile, source


class ExportTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="export-test-")
        self.db = os.path.join(self.tmp, "source.db")
        self.ckpt = os.path.join(self.tmp, "ckpt.json")
        self.out = os.path.join(self.tmp, "exports")
        self.conn = source.connect(self.db)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def export(self):
        return exporter.run_export(self.conn, self.ckpt, self.out)


class TestFirstExport(ExportTestBase):
    def test_first_export_is_full_and_creates_checkpoint(self):
        source.init_db(self.conn, rows=500, seed=1)
        result = self.export()
        self.assertEqual(result.kind, exporter.FULL)
        self.assertEqual(result.record_count, 500)
        self.assertTrue(result.fallback_reason)
        ckpt = ckpt_mod.load(self.ckpt)
        self.assertIsNotNone(ckpt)
        self.assertEqual(ckpt.version, source.current_seq(self.conn))
        self.assertEqual(ckpt.export_kind, exporter.FULL)


class TestNoChanges(ExportTestBase):
    def test_no_changes_returns_empty_incremental_fast(self):
        source.init_db(self.conn, rows=5000, seed=1)
        full = self.export()
        self.assertEqual(full.kind, exporter.FULL)

        started = time.perf_counter()
        inc = self.export()
        elapsed = time.perf_counter() - started

        self.assertEqual(inc.kind, exporter.INCREMENTAL)
        self.assertEqual(inc.record_count, 0, "无变化时增量必须为空")
        self.assertEqual(inc.from_version, inc.to_version)
        self.assertLess(elapsed, full.elapsed_seconds,
                        "无变化增量必须比全量快")
        # 空增量对拍：基线全量应用空增量 == 基线全量自身
        report = reconcile.verify(full.path, inc.path)
        self.assertEqual(report["differences"], 0)


class TestLargeChanges(ExportTestBase):
    def test_large_changes_incremental_matches_full(self):
        source.init_db(self.conn, rows=3000, seed=1)
        base = self.export()
        stats = source.mutate(self.conn, changes=2000, seed=2)
        inc = self.export()
        self.assertEqual(inc.kind, exporter.INCREMENTAL)
        # 同一行在一个批次内可能被多次修改，增量记录的是「越过位点的行」数
        changed_rows = self.conn.execute(
            "SELECT COUNT(*) FROM records WHERE version > ?",
            (base.to_version,)).fetchone()[0]
        self.assertEqual(inc.record_count, changed_rows)
        self.assertLessEqual(inc.record_count, sum(stats.values()))

        # 同一时刻再跑全量（导出期间无写入，两者严格同一时刻）
        ckpt2 = os.path.join(self.tmp, "ckpt2.json")
        full2 = exporter.run_export(self.conn, ckpt2, self.out)
        self.assertEqual(full2.kind, exporter.FULL)

        report = reconcile.verify(base.path, inc.path, full2.path)
        self.assertEqual(report["differences"], 0,
                         f"对拍失败: {report['problems'][:5]}")
        self.assertEqual(report["actual_records"], report["expected_records"])


class TestCheckpointLost(ExportTestBase):
    def test_missing_checkpoint_falls_back_to_full(self):
        source.init_db(self.conn, rows=300, seed=1)
        self.export()
        source.mutate(self.conn, changes=50, seed=2)
        os.unlink(self.ckpt)  # 位点丢失
        result = self.export()
        self.assertEqual(result.kind, exporter.FULL)
        self.assertTrue(result.fallback_reason)
        self.assertEqual(result.record_count,
                         len(reconcile.load_state(result.path)))

    def test_corrupt_checkpoint_falls_back_to_full(self):
        source.init_db(self.conn, rows=300, seed=1)
        self.export()
        with open(self.ckpt, "w", encoding="utf-8") as f:
            f.write('{"version": "not-a-number", "export_kind":')  # 写坏的位点
        result = self.export()
        self.assertEqual(result.kind, exporter.FULL)
        self.assertTrue(result.fallback_reason)
        # 回退全量后位点被重建，下一次恢复增量
        source.mutate(self.conn, changes=10, seed=3)
        result2 = self.export()
        self.assertEqual(result2.kind, exporter.INCREMENTAL)
        self.assertEqual(result2.record_count, 10)

    def test_stale_tmp_files_are_ignored(self):
        source.init_db(self.conn, rows=100, seed=1)
        self.export()
        # 模拟崩溃残留的临时位点文件
        for name in (".ckpt-aaa.tmp", ".ckpt-bbb.tmp"):
            with open(os.path.join(self.tmp, name), "w") as f:
                f.write("partial garbage")
        source.mutate(self.conn, changes=5, seed=2)
        result = self.export()
        self.assertEqual(result.kind, exporter.INCREMENTAL)
        self.assertEqual(result.record_count, 5)


class TestCrashRecovery(ExportTestBase):
    def test_crash_between_export_and_checkpoint_is_idempotent(self):
        """导出文件落盘后、位点推进前崩溃：下次从旧位点重导出重叠范围，
        按 version 幂等应用，最终状态仍与全量等价。"""
        source.init_db(self.conn, rows=500, seed=1)
        base = self.export()
        source.mutate(self.conn, changes=30, seed=2)

        # 模拟崩溃：直接调用底层增量导出（文件落盘），但不推进位点
        ckpt = ckpt_mod.load(self.ckpt)
        crashed_inc = exporter._export_incremental(self.conn, self.out,
                                                   ckpt.version)
        # 崩溃后重启：位点仍是旧值，重导出同一范围（重叠）
        retry_inc = self.export()
        self.assertEqual(retry_inc.kind, exporter.INCREMENTAL)
        self.assertEqual(retry_inc.from_version, crashed_inc.from_version)

        ckpt2 = os.path.join(self.tmp, "ckpt2.json")
        full_now = exporter.run_export(self.conn, ckpt2, self.out)
        state = reconcile.load_state(base.path)
        reconcile.apply_incremental(state, crashed_inc.path)   # 崩溃前写出的
        reconcile.apply_incremental(state, retry_inc.path)     # 重启后重导出的
        expected = reconcile.load_state(full_now.path)
        self.assertEqual(reconcile.diff_states(state, expected), [])

    def test_checkpoint_atomic_write_survives_torn_write(self):
        source.init_db(self.conn, rows=100, seed=1)
        self.export()
        before = ckpt_mod.load(self.ckpt)
        # 模拟在位点文件上直接发生撕裂写（绕过原子替换）
        with open(self.ckpt, "wb") as f:
            f.write(b'{"version": 99999999, "export_kind": "inc')
            f.flush()
            os.fsync(f.fileno())
        # load 必须识别损坏并回退，而不是读到假位点
        self.assertIsNone(ckpt_mod.load(self.ckpt))
        result = self.export()
        self.assertEqual(result.kind, exporter.FULL)
        after = ckpt_mod.load(self.ckpt)
        self.assertIsNotNone(after)
        self.assertGreaterEqual(after.version, before.version)


class TestDeletesPropagate(ExportTestBase):
    def test_deletes_are_exported_as_tombstones(self):
        source.init_db(self.conn, rows=200, seed=1)
        base = self.export()
        source.mutate(self.conn, changes=100, seed=2,
                      update_ratio=0.0, delete_ratio=1.0)  # 全部删除
        inc = self.export()
        with open(inc.path, encoding="utf-8") as f:
            header = json.loads(f.readline())
            records = [json.loads(line) for line in f]
        self.assertTrue(all(r["deleted"] == 1 for r in records))
        ckpt2 = os.path.join(self.tmp, "ckpt2.json")
        full_now = exporter.run_export(self.conn, ckpt2, self.out)
        report = reconcile.verify(base.path, inc.path, full_now.path)
        self.assertEqual(report["differences"], 0)


class TestRandomizedEquivalence(ExportTestBase):
    def test_multi_round_random_mutations_always_match_full(self):
        """多轮随机变更，每轮：增量导出 → 应用到状态 → 与同一时刻全量对拍。"""
        rng = random.Random(1234)
        source.init_db(self.conn, rows=1000, seed=1)
        base = self.export()
        state = reconcile.load_state(base.path)
        for round_no in range(8):
            source.mutate(self.conn, changes=rng.randint(0, 300),
                          seed=1000 + round_no)
            inc = self.export()
            self.assertEqual(inc.kind, exporter.INCREMENTAL)
            reconcile.apply_incremental(state, inc.path)
            ckpt_n = os.path.join(self.tmp, f"ckpt_r{round_no}.json")
            full_now = exporter.run_export(self.conn, ckpt_n, self.out)
            expected = reconcile.load_state(full_now.path)
            problems = reconcile.diff_states(state, expected)
            self.assertEqual(problems, [], f"第 {round_no} 轮对拍失败: {problems[:3]}")


if __name__ == "__main__":
    unittest.main()
