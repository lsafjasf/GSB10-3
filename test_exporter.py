#!/usr/bin/env python3
"""增量导出回归测试（仅标准库 unittest）。

覆盖：首次导出、无变化、大量变化、位点丢失、位点损坏、
      位点写入崩溃恢复、删除传播、增量幂等重放、增量链连续性。
"""
import json
import os
import tempfile
import unittest

from exporter import CheckpointStore, Exporter, SourceDB, materialize


def read_snapshot(path):
    with open(path, encoding="utf-8") as f:
        header = json.loads(f.readline())
        rows = [json.loads(line) for line in f]
    return header, rows


def read_delta(path):
    with open(path, encoding="utf-8") as f:
        header = json.loads(f.readline())
        ops = [json.loads(line) for line in f]
    return header, ops


class ExporterTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.db = SourceDB(os.path.join(d, "src.db"))
        self.ckpt = os.path.join(d, "checkpoint.json")
        self.exp = Exporter(self.db, self.ckpt)
        self.out = lambda name: os.path.join(d, name)

    def tearDown(self):
        self.dir.cleanup()

    def seed(self, n, start=0):
        self.db.bulk_upsert([(f"id-{i:06d}", f"payload-{i}")
                             for i in range(start, start + n)])

    # 1. 首次导出：无位点 -> 全量，并创建位点
    def test_first_export_is_full_and_creates_checkpoint(self):
        self.seed(100)
        info = self.exp.export_incremental(self.out("d0.jsonl"))
        self.assertEqual(info["kind"], "full")
        self.assertIn("fallback", info)
        self.assertEqual(info["rows"], 100)
        header, rows = read_snapshot(self.out("d0.jsonl"))
        self.assertEqual(header["type"], "full")
        self.assertEqual(len(rows), 100)
        self.assertEqual(CheckpointStore(self.ckpt).load(), header["version"])

    # 2. 无变化：增量快速返回空结果，位点不后退
    def test_no_change_returns_empty_delta(self):
        self.seed(100)
        self.exp.export_full(self.out("f0.jsonl"))
        v0 = CheckpointStore(self.ckpt).load()
        info = self.exp.export_incremental(self.out("d1.jsonl"))
        self.assertEqual(info["ops"], 0)
        header, ops = read_delta(self.out("d1.jsonl"))
        self.assertEqual((header["from"], header["to"]), (v0, v0))
        self.assertEqual(ops, [])
        self.assertEqual(CheckpointStore(self.ckpt).load(), v0)

    # 3. 大量变化：基线 + 增量回放 == 同时刻全量（对拍）
    def test_bulk_changes_equivalence(self):
        self.seed(10_000)
        self.exp.export_full(self.out("base.jsonl"))
        # 改 3000、删 2000、增 2000（三段 id 互不相交）
        self.db.bulk_upsert([(f"id-{i:06d}", f"updated-{i}")
                             for i in range(0, 3000)])
        self.db.bulk_delete([f"id-{i:06d}" for i in range(3000, 5000)])
        self.db.bulk_upsert([(f"new-{i:06d}", f"fresh-{i}")
                             for i in range(2000)])
        info = self.exp.export_incremental(self.out("d.jsonl"))
        self.assertEqual(info["ops"], 3000 + 2000 + 2000)
        materialize(self.out("base.jsonl"), [self.out("d.jsonl")],
                    self.out("mat.jsonl"))
        self.exp.export_full(self.out("full.jsonl"))
        with open(self.out("mat.jsonl"), "rb") as a, \
             open(self.out("full.jsonl"), "rb") as b:
            self.assertEqual(a.read(), b.read(), "增量回放与全量对拍不一致")

    # 4. 位点丢失：兜底为全量导出
    def test_checkpoint_loss_falls_back_to_full(self):
        self.seed(500)
        self.exp.export_full(self.out("f.jsonl"))
        os.unlink(self.ckpt)  # 位点丢失
        info = self.exp.export_incremental(self.out("d.jsonl"))
        self.assertEqual(info["kind"], "full")
        self.assertEqual(info["rows"], 500)
        _, rows = read_snapshot(self.out("d.jsonl"))
        self.assertEqual(len(rows), 500)

    # 5. 位点损坏（半写入的脏文件）：同样兜底全量
    def test_checkpoint_corrupted_falls_back_to_full(self):
        self.seed(200)
        self.exp.export_full(self.out("f.jsonl"))
        with open(self.ckpt, "w", encoding="utf-8") as f:
            f.write('{"version": 12')  # 截断的 JSON，模拟断电半写
        info = self.exp.export_incremental(self.out("d.jsonl"))
        self.assertEqual(info["kind"], "full")
        self.assertEqual(info["rows"], 200)

    # 6. 位点保存中途崩溃：旧位点保持有效，重导不丢不重
    def test_crash_during_checkpoint_save_keeps_old(self):
        self.seed(100)
        self.exp.export_full(self.out("f.jsonl"))
        v_old = CheckpointStore(self.ckpt).load()
        self.db.bulk_upsert([("id-000001", "changed")])
        real_replace = os.replace
        os.replace = lambda *_: (_ for _ in ()).throw(RuntimeError("crash"))
        try:
            with self.assertRaises(RuntimeError):
                CheckpointStore(self.ckpt).save(v_old + 1)
        finally:
            os.replace = real_replace
        self.assertEqual(CheckpointStore(self.ckpt).load(), v_old)
        # 崩溃后重新增量导出，仍能拿到变更
        info = self.exp.export_incremental(self.out("d.jsonl"))
        self.assertEqual(info["ops"], 1)

    # 7. 删除必须通过 tombstone 传播到下游
    def test_deletes_propagate(self):
        self.seed(50)
        self.exp.export_full(self.out("base.jsonl"))
        self.db.bulk_delete(["id-000007", "id-000042"])
        self.exp.export_incremental(self.out("d.jsonl"))
        materialize(self.out("base.jsonl"), [self.out("d.jsonl")],
                    self.out("mat.jsonl"))
        _, rows = read_snapshot(self.out("mat.jsonl"))
        ids = {r["id"] for r in rows}
        self.assertEqual(len(rows), 48)
        self.assertNotIn("id-000007", ids)
        self.assertNotIn("id-000042", ids)

    # 8. 崩溃重试导致同一增量被重复应用：幂等，结果不变
    def test_delta_reapply_is_idempotent(self):
        self.seed(100)
        self.exp.export_full(self.out("base.jsonl"))
        self.db.bulk_upsert([("id-000010", "v2"), ("id-000011", "v2")])
        self.db.bulk_delete(["id-000020"])
        self.exp.export_incremental(self.out("d.jsonl"))
        materialize(self.out("base.jsonl"),
                    [self.out("d.jsonl"), self.out("d.jsonl")],  # 应用两次
                    self.out("mat.jsonl"))
        self.exp.export_full(self.out("full.jsonl"))
        with open(self.out("mat.jsonl"), "rb") as a, \
             open(self.out("full.jsonl"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    # 9. 多段增量链：连续回放等价于一次全量
    def test_incremental_chain(self):
        self.seed(100)
        self.exp.export_full(self.out("base.jsonl"))
        deltas = []
        for round_ in range(5):
            self.db.bulk_upsert([(f"chain-{round_}-{i}", "x")
                                 for i in range(10)])
            p = self.out(f"d{round_}.jsonl")
            self.exp.export_incremental(p)
            deltas.append(p)
        materialize(self.out("base.jsonl"), deltas, self.out("mat.jsonl"))
        self.exp.export_full(self.out("full.jsonl"))
        with open(self.out("mat.jsonl"), "rb") as a, \
             open(self.out("full.jsonl"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    # 10. 高水位语义：导出进行中并发写入的数据属于下一轮，快照仍自洽
    def test_snapshot_is_deterministic_under_concurrent_writes(self):
        self.seed(100)
        v_before = self.db.max_version()
        # 模拟导出已取高水位后、读数据前发生的并发写入
        self.db.bulk_upsert([("late-1", "late")])
        rows = self.db.fetch_snapshot(v_before)
        self.assertEqual(len(rows), 100)
        self.assertNotIn("late-1", {r[0] for r in rows})


if __name__ == "__main__":
    unittest.main(verbosity=2)
