"""Named, self-contained scenarios used by the demo and the tests."""

from dataclasses import dataclass

from .lossmodel import build_script
from .random_loss import IndependentLossPolicy
from .simulator import Simulator


@dataclass(frozen=True)
class Scenario:
    name: str
    rounds: int
    rtts: object = 0.1
    losses: object = None
    initial_cwnd: int = 1
    ssthresh: object = 8
    mss_bytes: int = 1500
    losses_factory: object = None

    def build(self):
        losses = (self.losses_factory()
                  if self.losses_factory is not None else self.losses)
        return Simulator(initial_cwnd=self.initial_cwnd,
                         ssthresh=self.ssthresh,
                         mss_bytes=self.mss_bytes,
                         rtts=self.rtts,
                         losses=losses)

    def run(self):
        return self.build().run(self.rounds)


def _pad(entries, rounds):
    """Pad a loss script with clean rounds up to ``rounds`` entries."""
    return build_script(entries) + (build_script([0]) * max(0, rounds - len(entries)))


def standard_scenarios():
    """All scenarios, keyed by name (insertion order is stable)."""
    scenarios = [
        # Growth only: slow start doubles until ssthresh=8, then +1/RTT.
        Scenario(name="no_loss", rounds=14),
        # Single loss with enough later ACKs -> three dup ACKs -> fast path.
        Scenario(name="single_loss_triple_ack", rounds=12,
                 losses=_pad([0, 0, 0, 0, 0, 0, {"lost": 1}], 12)),
        # Single loss with too few later ACKs -> timeout path.
        Scenario(name="single_loss_timeout", rounds=12,
                 losses=_pad([0, 0, {"lost": 2}], 12)),
        # Three consecutive loss rounds, alternating fast / timeout / fast.
        Scenario(name="consecutive_losses", rounds=12,
                 losses=_pad([0, 0, 0, 0, 0, 0, "fast", "rto", "fast"], 12)),
        # Repeated timeouts: cwnd pinned at 1, RTO doubles each round.
        Scenario(name="rto_backoff", rounds=8,
                 losses=_pad([0, 0, 0, "rto", "rto", "rto"], 8)),
        # RTT jumps 10x mid-run: window math is unchanged, only time axis.
        Scenario(name="rtt_jump", rounds=12,
                 rtts=[0.05] * 6 + [0.5] * 6),
        # Seedable random loss: same seed -> identical sequence.
        Scenario(name="seeded_random_loss", rounds=30,
                 losses_factory=lambda: IndependentLossPolicy(
                     loss_rate=0.05, seed=122)),
    ]
    return {scenario.name: scenario for scenario in scenarios}
