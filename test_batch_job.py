"""批处理检查点/续跑回归测试（仅标准库 unittest）。

覆盖：完整跑完、中途中断续跑、检查点损坏、重复触发、
检查点写入中断、残留 tmp、空分片、重复 key 等边界。
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from batch_job import (
    BatchJob,
    Checkpoint,
    CorruptCheckpointError,
    CrashInjector,
    RecordSink,
    Shard,
    Record,
    SimulatedCrash,
    make_shards,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="batchckpt-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.ckpt_path = os.path.join(self.dir, "checkpoint.json")
        self.out_path = os.path.join(self.dir, "output.tsv")

    def make_job(self, shards, injector=None, events=None):
        emit = events.append if events is not None else None
        ckpt = Checkpoint.load(self.ckpt_path, on_event=emit)
        sink = RecordSink(self.out_path)
        return BatchJob(shards, ckpt, sink, injector=injector, on_event=emit)


class TestFullRun(Base):
    def test_full_run_completes_and_checkpoint_records_all_shards(self):
        shards = make_shards(4, 5)
        events = []
        summary = self.make_job(shards, events=events).run()

        self.assertEqual(summary["written"], 20)
        self.assertEqual(summary["skipped_duplicates"], 0)
        sink = RecordSink(self.out_path)
        self.assertEqual(len(sink.lines()), 20)

        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.completed_shards, [s.name for s in shards])
        self.assertEqual(ckpt.positions, {})  # 完成的分片位点已归档清除
        self.assertEqual(ckpt.resume_point([s.name for s in shards]), (None, 0))
        self.assertEqual(events[-1], {"event": "job_done"})

    def test_empty_shard_list_and_empty_shard(self):
        events = []
        self.make_job([], events=events).run()
        self.assertEqual([e["event"] for e in events], ["resume", "job_done"])

        shards = [Shard("empty", ()), Shard("one", (Record("k1", "v1"),))]
        summary = self.make_job(shards).run()
        self.assertEqual(summary["written"], 1)
        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.completed_shards, ["empty", "one"])


class TestInterruptResume(Base):
    def test_resume_starts_exactly_at_interrupt_point(self):
        shards = make_shards(4, 5)  # 共 20 条
        # 第一次运行：第 7 次提交后崩溃（shard-1 的第 3 条之后）
        events1 = []
        with self.assertRaises(SimulatedCrash):
            self.make_job(shards, injector=CrashInjector(fail_after=7), events=events1).run()

        commits1 = [e for e in events1 if e["event"] == "record_commit"]
        self.assertEqual(len(commits1), 7)
        interrupt_point = (commits1[-1]["shard"], commits1[-1]["position"])
        self.assertEqual(interrupt_point, ("shard-1", 2))  # 5 + 2：shard-1 的第 2 条

        # 中断现场：检查点记录的位点 == 最后一次提交的位点
        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.completed_shards, ["shard-0"])
        self.assertEqual(ckpt.position("shard-1"), 2)

        # 续跑：resume 事件报告的起点必须与中断点一致
        events2 = []
        summary = self.make_job(shards, events=events2).run()
        resume_event = next(e for e in events2 if e["event"] == "resume")
        self.assertEqual((resume_event["shard"], resume_event["position"]), interrupt_point)

        # 续跑跳过了已完成分片，第一条提交就是中断处的下一条
        self.assertIn({"event": "shard_skip", "shard": "shard-0"}, events2)
        commits2 = [e for e in events2 if e["event"] == "record_commit"]
        self.assertEqual((commits2[0]["shard"], commits2[0]["position"]), ("shard-1", 3))
        self.assertEqual(len(commits2), 13)

        # 最终结果与一次性跑完完全一致
        self.assertEqual(summary["written"], 13)
        self.assertEqual(summary["skipped_duplicates"], 0)
        sink = RecordSink(self.out_path)
        self.assertEqual(len(sink.lines()), 20)
        self.assertEqual(len(set(sink.lines())), 20)  # 无重复行

    def test_resume_after_crash_at_shard_boundary(self):
        """崩溃发生在"最后一条记录已提交、分片完成标记未落盘"的缝隙里：
        续跑从该分片的已提交位点继续，幂等汇保证不重写。"""
        shards = make_shards(3, 2)  # 6 条
        with self.assertRaises(SimulatedCrash):
            self.make_job(shards, injector=CrashInjector(fail_after=2)).run()
        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.completed_shards, [])  # shard-0 完成标记未落盘
        self.assertEqual(ckpt.position("shard-0"), 2)  # 但位点已记录

        events = []
        summary = self.make_job(shards, events=events).run()
        resume_event = next(e for e in events if e["event"] == "resume")
        self.assertEqual((resume_event["shard"], resume_event["position"]), ("shard-0", 2))
        self.assertEqual(summary["written"], 4)
        self.assertEqual(summary["skipped_duplicates"], 0)
        lines = RecordSink(self.out_path).lines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(len(set(lines)), 6)


class TestIdempotency(Base):
    def test_duplicate_trigger_has_no_side_effects(self):
        shards = make_shards(3, 4)
        self.make_job(shards).run()
        bytes_after_first = RecordSink(self.out_path).read_bytes()

        # 重复触发：同一任务再跑一遍
        events = []
        summary = self.make_job(shards, events=events).run()

        # 幂等断言 1：输出文件字节级不变
        self.assertEqual(RecordSink(self.out_path).read_bytes(), bytes_after_first)
        # 幂等断言 2：零写入、零提交事件，只有分片跳过
        self.assertEqual(summary, {"written": 0, "skipped_duplicates": 0})
        self.assertFalse([e for e in events if e["event"] == "record_commit"])
        skips = [e["shard"] for e in events if e["event"] == "shard_skip"]
        self.assertEqual(skips, [s.name for s in shards])

    def test_rerun_completed_shard_with_fresh_checkpoint_is_deduped(self):
        """检查点丢失（从零恢复）时，幂等汇保证已完成分片重跑也不产生重复副作用。"""
        shards = make_shards(2, 3)
        self.make_job(shards).run()
        os.unlink(self.ckpt_path)  # 检查点丢失，被迫从头再来

        summary = self.make_job(shards).run()
        self.assertEqual(summary["written"], 0)
        self.assertEqual(summary["skipped_duplicates"], 6)
        lines = RecordSink(self.out_path).lines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(len(set(lines)), 6)  # 幂等断言：无重复行

    def test_duplicate_keys_across_shards_are_written_once(self):
        shards = [
            Shard("a", (Record("k1", "v1"), Record("k2", "v2"))),
            Shard("b", (Record("k1", "v1-dup"), Record("k3", "v3"))),
        ]
        summary = self.make_job(shards).run()
        self.assertEqual(summary["written"], 3)
        self.assertEqual(summary["skipped_duplicates"], 1)
        lines = RecordSink(self.out_path).lines()
        self.assertEqual(len(lines), 3)
        self.assertIn("k1\tv1", lines)  # 保留首次写入的值


class TestCorruptCheckpoint(Base):
    def _run_all(self, shards):
        self.make_job(shards).run()

    def test_garbage_checkpoint_recovers_from_scratch_without_duplicates(self):
        shards = make_shards(3, 3)
        self._run_all(shards)
        bytes_before = RecordSink(self.out_path).read_bytes()

        with open(self.ckpt_path, "wb") as f:
            f.write(b"\x00\x01 not json {{{")
        events = []
        summary = self.make_job(shards, events=events).run()

        # 恢复事件 + 备份文件存在
        recovered = [e for e in events if e["event"] == "checkpoint_recovered"]
        self.assertEqual(len(recovered), 1)
        self.assertTrue(os.path.exists(recovered[0]["backup"]))
        # 从零重跑但幂等：无新副作用
        self.assertEqual(summary["written"], 0)
        self.assertEqual(summary["skipped_duplicates"], 9)
        self.assertEqual(RecordSink(self.out_path).read_bytes(), bytes_before)
        # 新检查点有效且记录全部完成
        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.completed_shards, [s.name for s in shards])

    def test_tampered_checksum_is_detected(self):
        shards = make_shards(2, 2)
        self._run_all(shards)
        with open(self.ckpt_path, "r", encoding="utf-8") as f:
            envelope = json.load(f)
        envelope["completed_shards"] = []  # 篡改内容但保留结构
        with open(self.ckpt_path, "w", encoding="utf-8") as f:
            json.dump(envelope, f)
        with self.assertRaises(CorruptCheckpointError):
            Checkpoint.load(self.ckpt_path, recover=False)
        # recover=True 时自动恢复
        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.completed_shards, [])

    def test_truncated_checkpoint_and_bad_version(self):
        shards = make_shards(2, 2)
        self._run_all(shards)
        with open(self.ckpt_path, "rb") as f:
            data = f.read()
        with open(self.ckpt_path, "wb") as f:
            f.write(data[: len(data) // 2])  # 截断
        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.completed_shards, [])

        with open(self.ckpt_path, "w", encoding="utf-8") as f:
            json.dump({"version": 999, "checksum": "x"}, f)
        with self.assertRaises(CorruptCheckpointError):
            Checkpoint.load(self.ckpt_path, recover=False)


class TestAtomicWrite(Base):
    def test_crash_during_checkpoint_write_keeps_old_valid_checkpoint(self):
        shards = make_shards(2, 3)
        # 先正常提交 1 条，让磁盘上存在有效检查点
        with self.assertRaises(SimulatedCrash):
            self.make_job(shards, injector=CrashInjector(fail_after=1)).run()
        with open(self.ckpt_path, "rb") as f:
            good_bytes = f.read()

        # 续跑时在 os.replace 处崩溃（模拟写到一半断电）
        injector = CrashInjector(fail_after=100)
        with mock.patch("os.replace", side_effect=SimulatedCrash("replace 时断电")):
            with self.assertRaises(SimulatedCrash):
                self.make_job(shards, injector=injector).run()

        # 正式检查点仍是崩溃前的有效版本，可正常加载续跑
        with open(self.ckpt_path, "rb") as f:
            self.assertEqual(f.read(), good_bytes)
        ckpt = Checkpoint.load(self.ckpt_path)
        self.assertEqual(ckpt.position("shard-0"), 1)

        events = []
        self.make_job(shards, events=events).run()
        resume_event = next(e for e in events if e["event"] == "resume")
        self.assertEqual((resume_event["shard"], resume_event["position"]), ("shard-0", 1))
        self.assertEqual(len(RecordSink(self.out_path).lines()), 6)

    def test_crash_while_writing_tmp_file_leaves_no_partial_checkpoint(self):
        shards = make_shards(2, 2)
        real_fdopen = os.fdopen

        def exploding_fdopen(*args, **kwargs):
            f = real_fdopen(*args, **kwargs)
            class ExplodingFile:
                def __init__(self, inner):
                    self._inner = inner
                def __enter__(self):
                    return self
                def __exit__(self, *exc):
                    self._inner.close()
                    return False
                def write(self, data):
                    self._inner.write(data[: len(data) // 2])  # 只写一半就断电
                    raise SimulatedCrash("tmp 写一半断电")
                def flush(self):
                    self._inner.flush()
                def fileno(self):
                    return self._inner.fileno()
            return ExplodingFile(f)

        with mock.patch("os.fdopen", side_effect=exploding_fdopen):
            with self.assertRaises(SimulatedCrash):
                self.make_job(shards).run()

        # 正式检查点不存在（从未 replace），残缺 tmp 已被清理或不影响加载
        self.assertFalse(os.path.exists(self.ckpt_path))
        ckpt = Checkpoint.load(self.ckpt_path)  # 不抛异常，从零开始
        self.assertEqual(ckpt.completed_shards, [])
        self.make_job(shards).run()
        self.assertEqual(len(RecordSink(self.out_path).lines()), 4)

    def test_leftover_tmp_file_is_ignored(self):
        shards = make_shards(2, 2)
        self.make_job(shards).run()
        with open(self.ckpt_path + ".tmp", "w", encoding="utf-8") as f:
            f.write("garbage partial write")
        ckpt = Checkpoint.load(self.ckpt_path)  # 不受 tmp 干扰
        self.assertEqual(ckpt.completed_shards, [s.name for s in shards])


if __name__ == "__main__":
    unittest.main(verbosity=2)
