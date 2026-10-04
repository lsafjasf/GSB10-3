# -*- coding: utf-8 -*-
"""复制日志拉取与续传的自测（标准库 unittest）。"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from replication import (  # noqa: E402
    Batch,
    CheckpointStore,
    FetchTimeout,
    FetchUnavailable,
    FlakyTransport,
    GapReport,
    LocalTransport,
    LogSource,
    PositionPurged,
    Puller,
    PullStats,
    Record,
    SequenceSink,
    UnrecoverableFetchError,
)


def no_sleep(_seconds):
    return None


class ReplicationTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="repl-test-")
        self.ckpt_path = os.path.join(self.tmpdir, "checkpoint.json")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def make_source(self, n):
        source = LogSource()
        source.append_many(["rec-%d" % i for i in range(n)])
        return source

    def make_puller(self, transport, sink, **kw):
        kw.setdefault("sleep", no_sleep)
        return Puller(transport, CheckpointStore(self.ckpt_path), sink, **kw)

    def assert_contiguous(self, sink, expected):
        """消费序列断言：与期望序列完全一致（有序、无缺、无重）。"""
        self.assertEqual(sink.applied_lsns, expected)
        self.assertEqual(sink.applied_lsns, sorted(set(sink.applied_lsns)))


class TestResume(ReplicationTestBase):
    def test_resume_after_restart_keeps_sequence(self):
        source = self.make_source(10)
        sink = SequenceSink()

        # 第一阶段：拉 2 批（batch_size=3 -> lsn 0..5），随后“进程退出”。
        puller1 = self.make_puller(LocalTransport(source), sink, batch_size=3)
        stats1 = puller1.pull_once(max_batches=2)
        self.assertEqual(stats1.records, 6)
        self.assertEqual(sink.applied_lsns, [0, 1, 2, 3, 4, 5])
        del puller1  # 模拟重启：丢弃内存状态，只留 checkpoint 文件。

        # 重启前位点已按批次落盘。
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 6)

        # 第二阶段：新 Puller 从已确认位点继续。
        puller2 = self.make_puller(LocalTransport(source), sink, batch_size=3)
        self.assertEqual(puller2.position, 6)
        stats2 = puller2.pull_once()
        self.assertEqual(stats2.records, 4)

        # 重启前后消费序列一致：恰好是 0..9，无缺无重。
        self.assert_contiguous(sink, list(range(10)))
        self.assertEqual(sink.applied_payloads,
                         ["rec-%d" % i for i in range(10)])
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 10)

    def test_crash_between_apply_and_checkpoint_replays_at_most_one_batch(self):
        # checkpoint 只提交到 3，但 sink 已 apply 到 5（崩溃窗口）。
        source = self.make_source(6)
        sink = SequenceSink()
        puller = self.make_puller(LocalTransport(source), sink, batch_size=3)
        puller.pull_once(max_batches=1)
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 3)
        # 手动模拟“apply 了第二批但没落盘就崩溃”。
        batch = source.fetch(3, 3, 1.0)
        for rec in batch.records:
            sink.apply(rec)
        del puller

        # 重启后从位点 3 重拉，3..5 为重复投递，必须被去重。
        puller2 = self.make_puller(LocalTransport(source), sink, batch_size=3)
        stats = puller2.pull_once()
        self.assertEqual(stats.duplicate_records, 3)
        self.assertEqual(stats.duplicate_batches, 1)
        self.assert_contiguous(sink, list(range(6)))


class TestPurgedPosition(ReplicationTestBase):
    def test_purged_position_reports_gap_and_does_not_skip(self):
        source = self.make_source(20)
        sink = SequenceSink()
        puller = self.make_puller(LocalTransport(source), sink, batch_size=5)
        puller.pull_once(max_batches=2)  # 消费到 lsn 9，位点=10
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 10)

        # 源端清理窗口前移：lsn < 15 的日志被清理，位点 10 已不可用。
        source.purge_below(15)
        del puller

        puller2 = self.make_puller(LocalTransport(source), sink, batch_size=5)
        stats = puller2.pull_once()

        # 必须显式报告缺口 [10, 14]，且不得静默跳到 15 继续。
        self.assertIsNotNone(stats.gap)
        self.assertEqual(stats.gap.requested_lsn, 10)
        self.assertEqual(stats.gap.earliest_available, 15)
        self.assertEqual((stats.gap.gap_start, stats.gap.gap_end), (10, 14))
        self.assertEqual(stats.gap.gap_size, 5)
        self.assertEqual(sink.applied_lsns, list(range(10)))  # 没有多消费
        self.assertEqual(puller2.position, 10)                # 位点未推进

        report = stats.gap.to_dict()
        self.assertEqual(report["type"], "log_gap")
        self.assertEqual(report["gap_interval"], [10, 14])
        json.dumps(report)  # 报告可序列化，便于上报

    def test_position_exactly_at_earliest_is_not_a_gap(self):
        source = self.make_source(8)
        source.purge_below(3)  # 保留 [3, 7]
        sink = SequenceSink()
        CheckpointStore(self.ckpt_path).save(3)
        puller = self.make_puller(LocalTransport(source), sink, batch_size=4)
        stats = puller.pull_once()
        self.assertIsNone(stats.gap)
        self.assert_contiguous(sink, [3, 4, 5, 6, 7])

    def test_source_fetch_raises_position_purged(self):
        source = self.make_source(5)
        source.purge_below(4)
        with self.assertRaises(PositionPurged) as ctx:
            source.fetch(1, 2, 1.0)
        self.assertEqual(ctx.exception.requested_lsn, 1)
        self.assertEqual(ctx.exception.earliest_available, 4)


class TestDuplicateBatch(ReplicationTestBase):
    def test_replayed_batch_is_deduplicated(self):
        source = self.make_source(6)
        sink = SequenceSink()
        # 先拿到第一批（lsn 0..2），稍后让传输层原样重投一次。
        replayed = source.fetch(0, 3, 1.0)
        transport = FlakyTransport(source, replay={2: replayed})
        puller = self.make_puller(transport, sink, batch_size=3)

        stats = puller.pull_once()
        # 调用序列：#1 正常批(0..2) -> #2 重投(0..2) -> #3 正常批(3..5) -> #4 None
        self.assertEqual(stats.duplicate_batches, 1)
        self.assertEqual(stats.duplicate_records, 3)
        self.assertEqual(stats.records, 6)
        self.assert_contiguous(sink, list(range(6)))
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 6)

    def test_partial_overlap_is_deduplicated(self):
        source = self.make_source(5)
        sink = SequenceSink()
        puller = self.make_puller(LocalTransport(source), sink, batch_size=3)
        puller.pull_once(max_batches=1)  # 消费 0..2，位点=3
        # 构造一个与已消费区间部分重叠的批次 [1, 3] 重投。
        overlap = Batch(
            start_lsn=1,
            end_lsn=3,
            records=tuple(Record(lsn, "rec-%d" % lsn) for lsn in (1, 2, 3)),
            batch_id="1-3",
        )
        st = PullStats()
        fresh = puller._apply_batch(overlap, st)
        self.assertEqual(fresh, 1)
        self.assertEqual(st.duplicate_records, 2)
        self.assertEqual(st.duplicate_batches, 0)  # 部分重叠不算整批重复
        self.assert_contiguous(sink, [0, 1, 2, 3])


class TestNetworkInterruption(ReplicationTestBase):
    def test_retries_recover_from_connection_drops(self):
        source = self.make_source(6)
        sink = SequenceSink()
        transport = FlakyTransport(source, drop_before=2)  # 前 2 次连接中断
        puller = self.make_puller(transport, sink, batch_size=3, max_retries=3)
        stats = puller.pull_once()
        self.assertEqual(stats.retries, 2)
        self.assert_contiguous(sink, list(range(6)))

    def test_timeout_is_retried_then_raises(self):
        source = self.make_source(6)
        sink = SequenceSink()
        # 响应延迟 0.05s 恒大于超时 0.01s -> 每次 fetch 都超时。
        transport = FlakyTransport(source, delay=0.05)
        puller = self.make_puller(
            transport, sink, batch_size=3,
            fetch_timeout=0.01, max_retries=2,
        )
        with self.assertRaises(UnrecoverableFetchError):
            puller.pull_once()
        # 重试期间位点不得推进、不得有半截批次落盘。
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 0)
        self.assertEqual(sink.applied_lsns, [])

    def test_outage_then_recovery_resumes_from_same_position(self):
        source = self.make_source(9)
        sink = SequenceSink()
        # 先正常消费一批（0..2）。
        puller = self.make_puller(LocalTransport(source), sink, batch_size=3)
        puller.pull_once(max_batches=1)
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 3)

        # 网络长时间中断：重试耗尽，抛错，位点保持 3。
        down = FlakyTransport(source, drop_before=100)
        puller_down = self.make_puller(down, sink, batch_size=3, max_retries=2)
        with self.assertRaises(UnrecoverableFetchError):
            puller_down.pull_once()
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 3)

        # 网络恢复后从位点 3 继续，序列仍然完整。
        puller_up = self.make_puller(LocalTransport(source), sink, batch_size=3)
        puller_up.pull_once()
        self.assert_contiguous(sink, list(range(9)))


class TestEdgeCases(ReplicationTestBase):
    def test_empty_source_returns_no_batch(self):
        source = LogSource()
        sink = SequenceSink()
        puller = self.make_puller(LocalTransport(source), sink)
        stats = puller.pull_once()
        self.assertEqual((stats.batches, stats.records), (0, 0))
        self.assertEqual(sink.applied_lsns, [])

    def test_batch_size_exact_multiple(self):
        source = self.make_source(6)
        sink = SequenceSink()
        puller = self.make_puller(LocalTransport(source), sink, batch_size=3)
        stats = puller.pull_once()
        self.assertEqual(stats.batches, 2)
        self.assert_contiguous(sink, list(range(6)))

    def test_missing_checkpoint_starts_at_zero(self):
        self.assertEqual(CheckpointStore(self.ckpt_path).load(), 0)

    def test_checkpoint_roundtrip_and_atomic_file(self):
        store = CheckpointStore(self.ckpt_path)
        store.save(42)
        self.assertEqual(store.load(), 42)
        with open(self.ckpt_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"next_lsn": 42})
        # 不残留临时文件。
        self.assertEqual(os.listdir(self.tmpdir), ["checkpoint.json"])

    def test_gap_report_serializable(self):
        report = GapReport(requested_lsn=7, earliest_available=12).to_dict()
        self.assertEqual(report["gap_interval"], [7, 11])
        self.assertEqual(report["gap_size"], 5)
        json.dumps(report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
