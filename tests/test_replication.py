"""End-to-end tests for the replication puller.

Scenarios required by the spec:
  1. normal resume after restart (same process and real subprocess restart)
  2. requested position already purged -> explicit gap report, no silent jump
  3. duplicate batch delivery -> dedup, duplicates counted
  4. network interruption -> timeout/retry, sequence stays consistent
Plus boundary cases (purge boundary, partial overlap, retry exhaustion,
checkpoint crash atomicity, empty log).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repl.log_source import LogSource
from repl.puller import GapError, Puller
from repl.server import LogServer

RECORDS = ["rec-%02d" % i for i in range(50)]


class PullerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.checkpoint = os.path.join(self.tmp.name, "checkpoint.json")
        self.gap_report = self.checkpoint + ".gaps.jsonl"
        self.source = LogSource(RECORDS)
        self.server = LogServer(self.source)
        self.server.__enter__()
        self.addCleanup(lambda: self.server.__exit__(None, None, None))
        self.consumed = []
        self.dense_from_zero = True  # gap/resync tests opt out below

    def make_puller(self, **kwargs):
        kwargs.setdefault("batch_size", 7)
        kwargs.setdefault("timeout", 0.3)
        kwargs.setdefault("backoff", 0.01)
        return Puller(self.server.host, self.server.port, self.checkpoint,
                      self.consume, **kwargs)

    def consume(self, position, record):
        if self.dense_from_zero:
            self.assertEqual(position, len(self.consumed),
                             "positions must be consumed densely and in order")
        self.consumed.append((position, record))

    def expected(self):
        return list(enumerate(RECORDS))


class TestResume(PullerTestBase):
    def test_resume_after_restart_same_process(self):
        first = self.make_puller()
        first.run_until_caught_up(max_batches=3)   # 3 batches = 21 records
        self.assertEqual(first.confirmed, 21)
        del first                                   # "restart"

        second = self.make_puller()                 # reloads checkpoint
        self.assertEqual(second.confirmed, 21)
        second.run_until_caught_up()

        self.assertEqual(self.consumed, self.expected())
        self.assertEqual(second.duplicate_batches, 0)

    def test_resume_after_real_process_restart(self):
        consumed_file = os.path.join(self.tmp.name, "consumed.log")
        base_cmd = [sys.executable, "-m", "repl.client_main",
                    "--host", self.server.host, "--port", str(self.server.port),
                    "--checkpoint", self.checkpoint,
                    "--consumed", consumed_file,
                    "--batch-size", "7"]
        env = dict(os.environ, PYTHONPATH=os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))

        # First incarnation: stop after 3 batches (simulates a restart).
        subprocess.run(base_cmd + ["--max-batches", "3"],
                       check=True, capture_output=True, text=True, env=env)
        with open(self.checkpoint) as fh:
            self.assertEqual(json.load(fh)["confirmed"], 21)

        # Second incarnation: resumes from the checkpoint, runs to the end.
        subprocess.run(base_cmd, check=True, capture_output=True, text=True, env=env)

        with open(consumed_file) as fh:
            lines = [line.rstrip("\n") for line in fh]
        expected_lines = ["%d %s" % (i, r) for i, r in enumerate(RECORDS)]
        self.assertEqual(lines, expected_lines,
                         "consumption sequence must be identical across restart")


class TestGap(PullerTestBase):
    def test_purged_position_reports_gap(self):
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=2)   # confirmed = 14
        self.source.purge_before(20)                # [14, 20) is gone

        with self.assertRaises(GapError) as ctx:
            puller.pull_batch()

        err = ctx.exception
        self.assertEqual(err.requested, 14)
        self.assertEqual(err.first_available, 20)
        self.assertIn("[14, 20)", str(err))
        # No silent jump: confirmed position is untouched.
        self.assertEqual(puller.confirmed, 14)

        with open(self.gap_report) as fh:
            report = json.loads(fh.readline())
        self.assertEqual(report["event"], "gap")
        self.assertEqual(report["missing_interval"], [14, 20])

    def test_position_exactly_at_purge_boundary_is_not_a_gap(self):
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=2)   # confirmed = 14
        self.source.purge_before(14)                # 14 is still available
        puller.run_until_caught_up()
        self.assertEqual(self.consumed, self.expected())

    def test_gap_then_manual_resync(self):
        self.dense_from_zero = False  # positions [7, 10) are intentionally lost
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=1)   # confirmed = 7
        self.source.purge_before(10)
        with self.assertRaises(GapError):
            puller.pull_batch()
        # Operator decision: skip the hole explicitly, never implicitly.
        puller.confirmed = self.source.first_available
        puller._save_checkpoint()
        puller.run_until_caught_up()
        positions = [p for p, _ in self.consumed]
        self.assertEqual(positions, list(range(7)) + list(range(10, 50)))


class TestDuplicates(PullerTestBase):
    def test_full_duplicate_batch_is_dropped(self):
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=2)   # confirmed = 14
        self.source.redeliver_last = True           # server replays [7, 14)
        puller.run_until_caught_up()
        self.assertEqual(puller.duplicate_batches, 1)
        self.assertEqual(self.consumed, self.expected())

    def test_partial_overlap_is_trimmed(self):
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=1)   # confirmed = 7
        # Inject an overlapping batch [4, 11): positions 4..6 are duplicates.
        self.source.last_response = {
            "type": "batch", "start": 4, "end": 11,
            "records": RECORDS[4:11],
        }
        self.source.redeliver_last = True
        puller.pull_batch()
        self.assertEqual(puller.duplicate_batches, 1)
        self.assertEqual(puller.confirmed, 11)
        self.assertEqual([p for p, _ in self.consumed], list(range(11)))

    def test_repeated_duplicates_all_counted(self):
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=1)
        for _ in range(3):
            self.source.redeliver_last = True
            puller.pull_batch()
        self.assertEqual(puller.duplicate_batches, 3)
        self.assertEqual([p for p, _ in self.consumed], list(range(7)))


class TestNetworkFaults(PullerTestBase):
    def test_dropped_connections_are_retried(self):
        self.source.drop_connections = 3
        puller = self.make_puller(max_retries=5)
        puller.run_until_caught_up()
        self.assertEqual(puller.retries, 3)
        self.assertEqual(self.consumed, self.expected())

    def test_hanging_connection_hits_timeout_and_recovers(self):
        self.source.hang_connections = 1            # never replies
        puller = self.make_puller(max_retries=3, timeout=0.1)
        puller.run_until_caught_up()
        self.assertGreaterEqual(puller.retries, 1)
        self.assertEqual(self.consumed, self.expected())

    def test_retry_exhaustion_raises_and_keeps_checkpoint(self):
        self.source.drop_connections = 10
        puller = self.make_puller(max_retries=2)
        with self.assertRaises(ConnectionError):
            puller.pull_batch()
        self.assertEqual(puller.retries, 2)
        self.assertEqual(puller.confirmed, 0)
        self.assertFalse(os.path.exists(self.checkpoint))

    def test_interruption_mid_stream_resumes_from_checkpoint(self):
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=2)   # confirmed = 14
        self.source.drop_connections = 2            # connection breaks...
        puller2 = self.make_puller()                # ...client restarts
        puller2.run_until_caught_up()
        self.assertEqual(puller2.retries, 2)
        self.assertEqual(self.consumed, self.expected())


class TestBoundaries(PullerTestBase):
    def test_empty_log_reports_up_to_date(self):
        self.source.purge_before(len(RECORDS))      # nothing left
        puller = self.make_puller(start_position=self.source.first_available)
        self.assertFalse(puller.pull_batch())
        self.assertEqual(self.consumed, [])

    def test_checkpoint_survives_crash_during_write(self):
        puller = self.make_puller()
        puller.run_until_caught_up(max_batches=1)
        # Simulate a crash mid-checkpoint: stale .tmp next to a good file.
        with open(self.checkpoint + ".tmp", "w") as fh:
            fh.write('{"confirmed": ')               # truncated garbage
        reloaded = self.make_puller()
        self.assertEqual(reloaded.confirmed, 7)
        reloaded.run_until_caught_up()
        self.assertEqual(self.consumed, self.expected())

    def test_checkpoint_written_once_per_batch(self):
        puller = self.make_puller()
        puller.pull_batch()
        with open(self.checkpoint) as fh:
            self.assertEqual(json.load(fh)["confirmed"], 7)

    def test_new_data_appearing_later_is_picked_up(self):
        self.dense_from_zero = False  # stream starts at position 50
        self.source.purge_before(len(RECORDS))
        puller = self.make_puller(start_position=50)
        self.assertFalse(puller.pull_batch())
        self.source.append(["rec-50", "rec-51"])
        puller.run_until_caught_up()
        self.assertEqual([r for _, r in self.consumed], ["rec-50", "rec-51"])


if __name__ == "__main__":
    unittest.main()
