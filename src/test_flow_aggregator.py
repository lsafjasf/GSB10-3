"""Unit tests for flow_aggregator. Run: python3 -m unittest -v"""

import unittest

from flow_aggregator import FlowAggregator, FlowRecord

K1 = dict(proto="tcp", src_ip="10.0.0.1", src_port=5000,
          dst_ip="10.0.0.2", dst_port=80)
K2 = dict(proto="tcp", src_ip="10.0.0.1", src_port=5001,
          dst_ip="10.0.0.2", dst_port=80)
TIMEOUT = 10.0


def rec(start, end, byte_count=100, packet_count=10, seq=0, **kw):
    kw.setdefault("proto", K1["proto"])
    kw.setdefault("src_ip", K1["src_ip"])
    kw.setdefault("src_port", K1["src_port"])
    kw.setdefault("dst_ip", K1["dst_ip"])
    kw.setdefault("dst_port", K1["dst_port"])
    return FlowRecord(start_ts=start, end_ts=end, byte_count=byte_count,
                      packet_count=packet_count, seq=seq, **kw)


class TestBasicMerging(unittest.TestCase):
    def test_single_record(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5, byte_count=300, packet_count=3))
        self.assertEqual(agg.active_keys(), 1)
        self.assertEqual(agg.active_sessions(), 1)
        (s,) = agg.flush()
        self.assertEqual((s.start_ts, s.end_ts), (0, 5))
        self.assertEqual((s.byte_count, s.packet_count, s.record_count),
                         (300, 3, 1))

    def test_gap_within_timeout_merges(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5))
        agg.add(rec(8, 12))  # gap = 3 <= 10
        (s,) = agg.flush()
        self.assertEqual((s.start_ts, s.end_ts), (0, 12))
        self.assertEqual(s.record_count, 2)

    def test_gap_at_exact_boundary_merges(self):
        """Boundary criterion: gap == idle_timeout merges."""
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5))
        agg.add(rec(15, 20))  # gap = 10 == idle_timeout
        sessions = agg.flush()
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0].start_ts, sessions[0].end_ts), (0, 20))

    def test_gap_beyond_boundary_splits(self):
        """Boundary criterion: gap > idle_timeout (strictly) splits."""
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5))
        agg.add(rec(15.000001, 20))  # gap = 10.000001 > 10
        sessions = agg.flush()
        self.assertEqual(len(sessions), 2)
        self.assertEqual((sessions[0].start_ts, sessions[0].end_ts), (0, 5))
        self.assertEqual((sessions[1].start_ts, sessions[1].end_ts),
                         (15.000001, 20))

    def test_gap_just_over_integer_boundary_splits(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5))
        agg.add(rec(16, 20))  # gap = 11 > 10
        self.assertEqual(len(agg.flush()), 2)

    def test_same_five_tuple_reused_after_timeout(self):
        """A five-tuple reused after the idle timeout must start a NEW
        session with a NEW session id."""
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5))
        agg.add(rec(100, 110))  # gap = 95 > 10 -> new session
        sessions = agg.flush()
        self.assertEqual(len(sessions), 2)
        self.assertNotEqual(sessions[0].session_id, sessions[1].session_id)
        self.assertEqual(sessions[0].key, sessions[1].key)

    def test_counters_accumulate_and_match_naive_sum(self):
        """Bytes/packets of merged sessions must equal the plain per-record
        sums; start/end must be min/max."""
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        records = [rec(0, 2, 10, 1), rec(3, 4, 20, 2), rec(5, 9, 30, 3)]
        for r in records:
            agg.add(r)
        (s,) = agg.flush()
        self.assertEqual(s.byte_count, sum(r.byte_count for r in records))
        self.assertEqual(s.packet_count,
                         sum(r.packet_count for r in records))
        self.assertEqual(s.start_ts, min(r.start_ts for r in records))
        self.assertEqual(s.end_ts, max(r.end_ts for r in records))
        self.assertEqual(s.record_count, len(records))

    def test_distinct_five_tuples_do_not_merge(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5, **K1))
        agg.add(rec(0, 5, **K2))  # different src_port
        self.assertEqual(agg.active_keys(), 2)
        self.assertEqual(len(agg.flush()), 2)


class TestOutOfOrder(unittest.TestCase):
    def test_late_record_merges_into_open_session(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5, 100, 1))
        agg.add(rec(30, 35, 100, 1))   # gap 25 > 10 -> second session
        agg.add(rec(8, 12, 100, 1))    # late: gap to first = 3 <= 10
        sessions = agg.flush()
        self.assertEqual(len(sessions), 2)
        first, second = sessions
        self.assertEqual((first.start_ts, first.end_ts), (0, 12))
        self.assertEqual(first.byte_count, 200)
        self.assertEqual((second.start_ts, second.end_ts), (30, 35))

    def test_late_record_bridges_two_sessions(self):
        """A late long record overlapping both neighbours must fuse them."""
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5, 100, 1))
        agg.add(rec(20, 25, 100, 1))   # separate session
        agg.add(rec(4, 21, 100, 1))    # late: bridges both
        sessions = agg.flush()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual((s.start_ts, s.end_ts), (0, 25))
        self.assertEqual(s.byte_count, 300)
        self.assertEqual(s.record_count, 3)

    def test_fully_reversed_arrival(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        for r in [rec(20, 25), rec(10, 15), rec(0, 5)]:  # reversed, gaps = 5
            agg.add(r)
        (s,) = agg.flush()
        self.assertEqual((s.start_ts, s.end_ts), (0, 25))
        self.assertEqual(s.record_count, 3)


class TestAgingAndMemoryBound(unittest.TestCase):
    def test_sweep_expires_idle_keys(self):
        clock = [0.0]
        agg = FlowAggregator(idle_timeout=TIMEOUT, clock=lambda: clock[0])
        agg.add(rec(0, 5))
        clock[0] = 20.0  # idle for 15 > 10
        expired = agg.sweep()
        self.assertEqual(len(expired), 1)
        self.assertEqual(agg.active_keys(), 0)
        self.assertEqual(agg.expired_keys, 1)

    def test_sweep_boundary_criterion(self):
        """last_seen == now - idle_timeout is retained; strictly older
        expires."""
        clock = [0.0]
        agg = FlowAggregator(idle_timeout=TIMEOUT, clock=lambda: clock[0])
        agg.add(rec(0, 5, **K1))
        agg.add(rec(0, 4, **K2))
        clock[0] = 15.0  # K1 idle exactly 10 (retained), K2 idle 11 (expired)
        expired = agg.sweep()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].key,
                         (K2["proto"], K2["src_ip"], K2["src_port"],
                          K2["dst_ip"], K2["dst_port"]))
        self.assertEqual(agg.active_keys(), 1)

    def test_sweep_uses_injected_now(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT)
        agg.add(rec(0, 5))
        self.assertEqual(agg.sweep(now=15.0), [])   # idle exactly 10: alive
        self.assertEqual(len(agg.sweep(now=15.1)), 1)  # idle 10.1: expired

    def test_lru_eviction_bounds_active_keys(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT, max_active_sessions=3)
        evicted_all = []
        for port in range(10):
            evicted_all += agg.add(rec(0, 5, src_port=1000 + port))
            self.assertLessEqual(agg.active_keys(), 3)
        # oldest keys were evicted with their sessions returned
        self.assertEqual(agg.evicted_keys, 7)
        self.assertEqual(len(evicted_all), 7)
        self.assertEqual(len(agg.flush()), 3)

    def test_lru_touch_refreshes_recency(self):
        agg = FlowAggregator(idle_timeout=TIMEOUT, max_active_sessions=2)
        agg.add(rec(0, 5, src_port=1))
        agg.add(rec(0, 5, src_port=2))
        agg.add(rec(1, 2, src_port=1))   # refresh key with port 1
        evicted = agg.add(rec(0, 5, src_port=3))  # evicts port 2, not port 1
        self.assertEqual(len(evicted), 1)
        self.assertEqual(evicted[0].key[2], 2)

    def test_memory_bound_independent_of_record_volume(self):
        """Feeding many records to few keys keeps active key count constant."""
        agg = FlowAggregator(idle_timeout=TIMEOUT, max_active_sessions=100)
        for i in range(10000):
            agg.add(rec(float(i), float(i) + 0.5, src_port=i % 5))
        self.assertEqual(agg.active_keys(), 5)


if __name__ == "__main__":
    unittest.main()
