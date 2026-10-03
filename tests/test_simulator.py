"""Self-tests for the deterministic send-window simulator.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sendwindow import (CongestionSender, IndependentLossPolicy, Simulator,
                        VirtualClock, rows_to_csv, sha256_text)
from sendwindow.scenarios import standard_scenarios
from sendwindow.simulator import CSV_FIELDS, EVENT_FAST, EVENT_OK, EVENT_RTO


def run_sim(**kwargs):
    rounds = kwargs.pop("rounds", None)
    return Simulator(**kwargs).run(rounds)


class TestGrowth(unittest.TestCase):
    def test_slow_start_doubles_until_ssthresh(self):
        result = run_sim(rounds=6, ssthresh=8)
        self.assertEqual(result.cwnd_series, [2, 4, 8, 9, 10, 11])
        self.assertEqual(result.events, [EVENT_OK] * 6)
        self.assertEqual(result.samples[3].phase_after,
                         "congestion_avoidance")

    def test_congestion_avoidance_is_additive(self):
        result = run_sim(rounds=4, initial_cwnd=8, ssthresh=8)
        self.assertEqual(result.cwnd_series, [9, 10, 11, 12])

    def test_growth_does_not_overshoot_ssthresh(self):
        sender = CongestionSender(initial_cwnd=7, ssthresh=8)
        sender.on_round_acked()
        self.assertEqual(sender.cwnd, 8)


class TestBackoff(unittest.TestCase):
    def test_triple_dupack_halves_window(self):
        result = run_sim(rounds=8, ssthresh=8,
                         losses=[0, 0, 0, 0, 0, 0, {"lost": 1}])
        self.assertEqual(result.cwnd_series[:6], [2, 4, 8, 9, 10, 11])
        self.assertEqual(result.events[6], EVENT_FAST)
        self.assertEqual(result.samples[6].ssthresh_after, 5)
        self.assertEqual(result.cwnd_series[6:], [5, 6])

    def test_timeout_resets_window_to_one(self):
        result = run_sim(rounds=6, ssthresh=8, losses=[0, 0, {"lost": 2}])
        self.assertEqual(result.events[2], EVENT_RTO)
        self.assertEqual(result.samples[2].ssthresh_after, 2)
        self.assertEqual(result.cwnd_series, [2, 4, 1, 2, 3, 4])

    def test_consecutive_losses_alternating_reactions(self):
        result = run_sim(rounds=10, ssthresh=8,
                         losses=[0, 0, 0, 0, 0, 0, "fast", "rto", "fast"])
        self.assertEqual(result.cwnd_series, [2, 4, 8, 9, 10, 11, 5, 1, 2, 3])
        self.assertEqual(result.events[6:9],
                         [EVENT_FAST, EVENT_RTO, EVENT_FAST])
        self.assertEqual(result.samples[7].ssthresh_after, 2)

    def test_repeated_timeouts_back_off_exponentially(self):
        result = run_sim(rounds=6, ssthresh=8,
                         losses=[0, 0, 0, "rto", "rto", "rto"])
        durations = [s.duration_s for s in result.samples[3:]]
        self.assertEqual(durations, [0.2, 0.4, 0.8])
        self.assertEqual(result.cwnd_series[3:], [1, 1, 1])
        times = [s.time_start_s for s in result.samples]
        self.assertAlmostEqual(times[5], 0.3 + 0.2 + 0.4)

    def test_backoff_resets_after_clean_round(self):
        result = run_sim(rounds=6, ssthresh=8,
                         losses=[0, "rto", 0, "rto"])
        durations = [s.duration_s for s in result.samples]
        self.assertEqual(durations[1], 0.2)
        self.assertEqual(durations[3], 0.2)


class TestRttJump(unittest.TestCase):
    def test_window_math_ignores_rtt_magnitude(self):
        slow = run_sim(rounds=12, rtts=[0.05] * 6 + [0.5] * 6)
        flat = run_sim(rounds=12, rtts=0.1)
        self.assertEqual(slow.cwnd_series, flat.cwnd_series)
        self.assertEqual(slow.cwnd_series,
                         [2, 4, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17])

    def test_time_axis_reflects_jump(self):
        result = run_sim(rounds=12, rtts=[0.05] * 6 + [0.5] * 6)
        times = [s.time_start_s for s in result.samples]
        self.assertAlmostEqual(times[6], 0.30)
        self.assertAlmostEqual(times[11], 0.30 + 5 * 0.5)
        throughputs = [s.steady_throughput_Bps for s in result.samples]
        self.assertAlmostEqual(throughputs[0], 1 * 1500 / 0.05)
        self.assertAlmostEqual(throughputs[6], 11 * 1500 / 0.5)


class TestReproducibility(unittest.TestCase):
    def test_same_inputs_same_bytes(self):
        scenario = standard_scenarios()["consecutive_losses"]
        first = rows_to_csv(scenario.run().samples)
        second = rows_to_csv(scenario.run().samples)
        self.assertEqual(first, second)
        self.assertEqual(sha256_text(first), sha256_text(second))

    def test_seeded_random_loss_is_replayable(self):
        def make():
            return Simulator(rtts=0.1,
                             losses=IndependentLossPolicy(0.2, seed=7))
        first = make().run(40)
        second = make().run(40)
        self.assertEqual(rows_to_csv(first.samples),
                         rows_to_csv(second.samples))
        self.assertTrue(any(e != EVENT_OK for e in first.events))

    def test_all_standard_scenarios_reproduce(self):
        for name, scenario in standard_scenarios().items():
            with self.subTest(scenario=name):
                self.assertEqual(
                    rows_to_csv(scenario.run().samples),
                    rows_to_csv(scenario.run().samples))

    def test_no_wall_clock_dependency(self):
        import sendwindow.clock as clock_module
        import sendwindow.simulator as simulator_module
        for module in (clock_module, simulator_module):
            source = open(module.__file__).read()
            self.assertNotIn("import time", source)
            self.assertNotIn("datetime", source)


class TestBoundaries(unittest.TestCase):
    def test_zero_rounds_exports_header_only(self):
        result = run_sim(rounds=0)
        text = rows_to_csv(result.samples)
        self.assertEqual(text.strip(), ",".join(CSV_FIELDS))

    def test_loss_at_first_round(self):
        result = run_sim(rounds=3, losses=[{"lost": 1}])
        self.assertEqual(result.events[0], EVENT_RTO)
        self.assertEqual(result.cwnd_series, [1, 2, 3])

    def test_loss_clamped_to_window(self):
        result = run_sim(rounds=3, losses=[0, 0, {"lost": 99}])
        self.assertEqual(result.samples[2].lost_mss, 4)
        self.assertEqual(result.events[2], EVENT_RTO)

    def test_ssthresh_floor_of_two(self):
        result = run_sim(rounds=2, initial_cwnd=3, ssthresh=3,
                         losses=[{"lost": 1}])
        self.assertEqual(result.samples[0].ssthresh_after, 2)
        self.assertEqual(result.samples[0].cwnd_after_mss, 1)

    def test_clock_cannot_go_backwards(self):
        clock = VirtualClock()
        with self.assertRaises(ValueError):
            clock.advance(-1.0)

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            Simulator(rtts=0.0)
        with self.assertRaises(ValueError):
            Simulator(rtts=[0.1, -0.5])
        with self.assertRaises(ValueError):
            Simulator(rtts=[])
        with self.assertRaises(ValueError):
            CongestionSender(initial_cwnd=0)
        with self.assertRaises(ValueError):
            IndependentLossPolicy(1.5, seed=1)
        with self.assertRaises(ValueError):
            Simulator(losses=[{"lost": 1, "reaction": "bogus"}])

    def test_export_round_trip(self):
        result = run_sim(rounds=5, ssthresh=4)
        text = rows_to_csv(result.samples)
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(parsed), 5)
        self.assertEqual([int(row["cwnd_after_mss"]) for row in parsed],
                         result.cwnd_series)
        self.assertEqual(parsed[0]["ssthresh_before"], "4")


if __name__ == "__main__":
    unittest.main()
