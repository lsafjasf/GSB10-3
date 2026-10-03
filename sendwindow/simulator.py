"""Round-based send-window simulator.

One iteration is one RTT of windowed transmission. The simulator only
advances an injected :class:`~sendwindow.clock.VirtualClock`, so identical
(rtt schedule, loss script, initial state) inputs produce identical window
sequences and identical timestamps.

RTO model: ``base_rto = max(2 * rtt, 0.2 s)`` (RFC 6298 smoothing omitted for
determinism). Each consecutive timeout doubles the effective RTO; the
back-off exponent resets to zero on a round that ends without a timeout.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .clock import VirtualClock
from .congestion import CongestionSender
from .lossmodel import (FORCE_FAST, FORCE_RTO, LossSpec, build_script,
                        coerce_spec)

EVENT_OK = "ok"
EVENT_FAST = "fast_retransmit"
EVENT_RTO = "timeout"

MIN_RTO_SECONDS = 0.2

CSV_FIELDS = [
    "round", "time_start_s", "rtt_s", "duration_s",
    "cwnd_before_mss", "ssthresh_before", "lost_mss", "acks_seen_mss",
    "event", "ssthresh_after", "cwnd_after_mss", "phase_after",
    "delivered_mss", "steady_throughput_Bps",
]


@dataclass(frozen=True)
class WindowSample:
    round: int
    time_start_s: float
    rtt_s: float
    duration_s: float
    cwnd_before_mss: int
    ssthresh_before: object
    lost_mss: int
    acks_seen_mss: int
    event: str
    ssthresh_after: object
    cwnd_after_mss: int
    phase_after: str
    delivered_mss: int
    steady_throughput_Bps: float


@dataclass(frozen=True)
class RunResult:
    samples: tuple
    config: dict

    @property
    def cwnd_series(self):
        return [s.cwnd_after_mss for s in self.samples]

    @property
    def events(self):
        return [s.event for s in self.samples]


class Simulator:
    def __init__(self, initial_cwnd=1, ssthresh=8, mss_bytes=1500,
                 rtts=0.1, losses=None, clock_start=0.0):
        self.sender = CongestionSender(initial_cwnd=initial_cwnd,
                                       ssthresh=ssthresh,
                                       mss_bytes=mss_bytes)
        if isinstance(rtts, Sequence) and not isinstance(rtts, (str, bytes)):
            self._rtt_schedule = [float(r) for r in rtts]
            if not self._rtt_schedule:
                raise ValueError("rtt schedule must not be empty")
        else:
            self._rtt_schedule = [float(rtts)]
        if any(r <= 0.0 for r in self._rtt_schedule):
            raise ValueError("every RTT must be positive")
        self._loss_policy = losses if callable(losses) else None
        self._loss_script = () if callable(losses) else build_script(losses)
        self.clock = VirtualClock(clock_start)
        self.config = {
            "initial_cwnd_mss": int(initial_cwnd),
            "ssthresh_mss": None if ssthresh is None else int(ssthresh),
            "mss_bytes": int(mss_bytes),
            "rtts_s": list(self._rtt_schedule),
            "loss_script": [
                {"lost": e.lost, "reaction": e.reaction}
                for e in self._loss_script
            ],
            "loss_policy": repr(losses) if callable(losses) else None,
            "clock_start_s": float(clock_start),
        }

    def _rtt_at(self, index):
        return (self._rtt_schedule[index]
                if index < len(self._rtt_schedule)
                else self._rtt_schedule[-1])

    def _loss_at(self, index, cwnd):
        if self._loss_policy is not None:
            return coerce_spec(index, cwnd, self._loss_policy)
        if index < len(self._loss_script):
            return self._loss_script[index]
        return LossSpec(0)

    def run(self, rounds=None):
        if rounds is None:
            if self._loss_policy is not None:
                raise ValueError("rounds is required with a loss policy")
            rounds = max(len(self._loss_script), len(self._rtt_schedule))
        if rounds < 0:
            raise ValueError("rounds must be non-negative")

        samples = []
        backoff = 0
        for index in range(rounds):
            rtt = self._rtt_at(index)
            base_rto = max(2.0 * rtt, MIN_RTO_SECONDS)
            cwnd_before = self.sender.cwnd
            ssthresh_before = self.sender.ssthresh
            start_time = self.clock.now()

            spec = self._loss_at(index, cwnd_before)
            lost = min(spec.lost, cwnd_before)
            acks_seen = cwnd_before - lost

            if spec.has_loss:
                reaction = spec.resolved_reaction(cwnd_before)
            else:
                reaction = None

            if reaction == FORCE_RTO:
                event = EVENT_RTO
                duration = base_rto * (2 ** backoff)
                backoff += 1
            else:
                event = EVENT_FAST if reaction == FORCE_FAST else EVENT_OK
                duration = rtt
                backoff = 0

            if event == EVENT_RTO:
                self.sender.on_timeout()
            elif event == EVENT_FAST:
                self.sender.on_triple_dupack()
            else:
                self.sender.on_round_acked()

            delivered = 0 if event == EVENT_RTO else acks_seen
            throughput = (delivered * self.sender.mss_bytes / duration
                          if duration > 0 else 0.0)

            sample = WindowSample(
                round=index,
                time_start_s=start_time,
                rtt_s=rtt,
                duration_s=duration,
                cwnd_before_mss=cwnd_before,
                ssthresh_before=ssthresh_before,
                lost_mss=lost,
                acks_seen_mss=acks_seen,
                event=event,
                ssthresh_after=self.sender.ssthresh,
                cwnd_after_mss=self.sender.cwnd,
                phase_after=self.sender.phase().value,
                delivered_mss=delivered,
                steady_throughput_Bps=throughput,
            )
            samples.append(sample)
            self.clock.advance(duration)

        return RunResult(samples=tuple(samples),
                         config=dict(self.config))
