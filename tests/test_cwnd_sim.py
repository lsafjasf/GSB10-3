import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwnd_sim import (
    ConstantRtt,
    ManualClock,
    NoLoss,
    RandomLoss,
    ScriptLoss,
    SimConfig,
    Simulator,
    StepRtt,
    samples_to_csv,
    simulate,
)


def cwnd_trace(samples):
    return [s.cwnd_after for s in samples]


class TestGrowthPaths(unittest.TestCase):
    def test_slow_start_doubles_until_ssthresh(self):
        samples = simulate(9, init_cwnd=1.0, init_ssthresh=64.0)
        self.assertEqual(
            cwnd_trace(samples), [2, 4, 8, 16, 32, 64, 65, 66, 67]
        )
        events = [s.event for s in samples]
        self.assertEqual(events[:6], ["slow_start"] * 6)
        self.assertEqual(events[6:], ["cong_avoid"] * 3)

    def test_congestion_avoidance_additive_increase(self):
        samples = simulate(5, init_cwnd=8.0, init_ssthresh=8.0)
        self.assertEqual(cwnd_trace(samples), [9, 10, 11, 12, 13])
        self.assertTrue(all(s.event == "cong_avoid" for s in samples))

    def test_slow_start_capped_exactly_at_ssthresh(self):
        samples = simulate(2, init_cwnd=6.0, init_ssthresh=8.0)
        self.assertEqual(cwnd_trace(samples), [8, 9])


class TestBackoffPaths(unittest.TestCase):
    def test_single_loss_fast_retransmit_halves_cwnd(self):
        samples = simulate(12, loss_model=ScriptLoss({8: [0]}),
                           init_cwnd=1.0, init_ssthresh=64.0)
        pre = samples[7].cwnd_after
        hit = samples[8]
        self.assertEqual(hit.event, "fast_retransmit")
        self.assertEqual(hit.lost, 1)
        self.assertEqual(hit.ssthresh, 64.0)
        self.assertEqual(hit.cwnd_after, max(pre / 2.0, 2.0))
        self.assertEqual(samples[9].event, "cong_avoid")
        self.assertEqual(samples[9].cwnd_after, hit.cwnd_after + 1.0)

    def test_multi_loss_timeout_resets_to_one(self):
        samples = simulate(12, loss_model=ScriptLoss({8: [0, 1]}),
                           init_cwnd=1.0, init_ssthresh=64.0)
        hit = samples[8]
        self.assertEqual(hit.event, "timeout")
        self.assertEqual(hit.cwnd_after, 1.0)
        self.assertEqual(samples[9].event, "slow_start")
        self.assertEqual(samples[9].cwnd_after, 2.0)

    def test_consecutive_losses_across_flights(self):
        loss = ScriptLoss({8: [0], 9: [0], 10: [0, 1]})
        samples = simulate(16, loss_model=loss)
        events = [s.event for s in samples]
        self.assertEqual(events[8], "fast_retransmit")
        self.assertEqual(events[9], "fast_retransmit")
        self.assertEqual(events[10], "timeout")
        self.assertEqual(samples[10].cwnd_after, 1.0)
        self.assertEqual(events[11], "slow_start")

    def test_ssthresh_floor_on_loss_at_small_cwnd(self):
        samples = simulate(3, loss_model=ScriptLoss({0: [0]}),
                           init_cwnd=2.0, init_ssthresh=64.0)
        hit = samples[0]
        self.assertEqual(hit.event, "fast_retransmit")
        self.assertEqual(hit.cwnd_after, 2.0)
        self.assertEqual(samples[1].ssthresh, 2.0)


class TestRttSpike(unittest.TestCase):
    def test_rtt_spike_changes_wall_time_not_growth(self):
        rtt = StepRtt([(0, 100.0), (4, 400.0), (8, 100.0)])
        samples = simulate(12, rtt_profile=rtt, init_cwnd=4.0, init_ssthresh=64.0)
        self.assertEqual(cwnd_trace(samples), [8, 16, 32, 64, 65, 66, 67, 68, 69, 70, 71, 72])
        self.assertEqual([s.rtt_ms for s in samples],
                         [100.0] * 4 + [400.0] * 4 + [100.0] * 4)
        expected_t = [0.0]
        for s in samples[:-1]:
            expected_t.append(expected_t[-1] + s.rtt_ms)
        self.assertEqual([s.t_send_ms for s in samples], expected_t)
        total = 4 * 100.0 + 4 * 400.0 + 4 * 100.0
        self.assertEqual(samples[-1].t_ack_ms, total)


class TestReproducibility(unittest.TestCase):
    def test_same_inputs_same_results(self):
        kwargs = dict(
            flights=24,
            loss_model=ScriptLoss({8: [0], 12: [0, 1]}),
            rtt_profile=StepRtt([(0, 100.0), (10, 250.0)]),
        )
        a = simulate(**kwargs)
        b = simulate(**kwargs)
        self.assertEqual(a, b)
        self.assertEqual(samples_to_csv(a), samples_to_csv(b))

    def test_random_loss_deterministic_per_seed(self):
        a = simulate(30, loss_model=RandomLoss(seed=42, loss_rate=0.2))
        b = simulate(30, loss_model=RandomLoss(seed=42, loss_rate=0.2))
        self.assertEqual(a, b)

    def test_no_real_clock_dependency(self):
        clock = ManualClock(start_ms=5000.0)
        samples = simulate(4, rtt_profile=ConstantRtt(50.0), clock=clock)
        self.assertEqual([s.t_send_ms for s in samples],
                         [5000.0, 5050.0, 5100.0, 5150.0])
        self.assertEqual(clock.now(), 5200.0)


class TestEdgeCases(unittest.TestCase):
    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            SimConfig(flights=0).validate()
        with self.assertRaises(ValueError):
            SimConfig(flights=1, init_cwnd=0.5).validate()
        with self.assertRaises(ValueError):
            SimConfig(flights=1, max_cwnd=0.5).validate()

    def test_invalid_rtt_profile_rejected(self):
        with self.assertRaises(ValueError):
            StepRtt([(3, 100.0)])
        with self.assertRaises(ValueError):
            StepRtt([(0, 0.0)])
        with self.assertRaises(ValueError):
            ConstantRtt(-1.0)

    def test_clock_cannot_go_backwards(self):
        clock = ManualClock()
        with self.assertRaises(ValueError):
            clock.advance(-1.0)

    def test_random_loss_rate_bounds(self):
        with self.assertRaises(ValueError):
            RandomLoss(seed=1, loss_rate=1.5)

    def test_max_cwnd_cap(self):
        samples = simulate(8, init_cwnd=1.0, init_ssthresh=64.0, max_cwnd=10.0)
        self.assertEqual(cwnd_trace(samples), [2, 4, 8, 10, 10, 10, 10, 10])

    def test_script_loss_positions_beyond_flight_ignored(self):
        samples = simulate(3, loss_model=ScriptLoss({0: [99]}), init_cwnd=2.0)
        self.assertEqual(samples[0].lost, 0)
        self.assertEqual(samples[0].event, "slow_start")

    def test_full_loss_flight_triggers_timeout(self):
        samples = simulate(4, loss_model=ScriptLoss({2: [0, 1, 2, 3]}),
                           init_cwnd=1.0, init_ssthresh=64.0)
        self.assertEqual(samples[2].event, "timeout")
        self.assertEqual(samples[2].cwnd_after, 1.0)


if __name__ == "__main__":
    unittest.main()
