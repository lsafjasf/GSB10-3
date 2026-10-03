"""Reno-style congestion-window simulator.

Time is discrete at RTT granularity: every flight sends one window of
packets, the clock advances by the flight's RTT, then ACKs are processed
and cwnd is updated. No real clock and no real I/O are involved.

Rules (classic Reno, per-RTT model):
  slow start (cwnd < ssthresh):   cwnd doubles each RTT (capped at ssthresh)
  congestion avoidance:           cwnd += 1 MSS per RTT
  single loss in a flight:        fast retransmit -> ssthresh=cwnd/2,
                                  cwnd=ssthresh (min floor 2 MSS)
  two or more losses in a flight: timeout (RTO) -> ssthresh=cwnd/2,
                                  cwnd=1 MSS, slow start resumes
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional

from .clock import ManualClock
from .loss import LossModel, NoLoss
from .rtt import RttProfile, ConstantRtt

MSS = 1
MIN_SSTHRESH = 2.0
MIN_FAST_RECOVERY_CWND = 2.0
RTO_CWND = 1.0


@dataclass(frozen=True)
class SimConfig:
    flights: int
    init_cwnd: float = 1.0
    init_ssthresh: float = 64.0
    max_cwnd: Optional[float] = None

    def validate(self) -> None:
        if self.flights < 1:
            raise ValueError("flights must be >= 1")
        if self.init_cwnd < 1.0:
            raise ValueError("init_cwnd must be >= 1 MSS")
        if self.init_ssthresh < 1.0:
            raise ValueError("init_ssthresh must be >= 1 MSS")
        if self.max_cwnd is not None and self.max_cwnd < 1.0:
            raise ValueError("max_cwnd must be >= 1 MSS")


@dataclass(frozen=True)
class WindowSample:
    flight: int
    t_send_ms: float
    rtt_ms: float
    cwnd: float
    ssthresh: float
    flight_size: int
    lost: int
    event: str
    cwnd_after: float
    t_ack_ms: float

    def to_row(self) -> dict:
        return asdict(self)


class Simulator:
    def __init__(
        self,
        config: SimConfig,
        loss_model: Optional[LossModel] = None,
        rtt_profile: Optional[RttProfile] = None,
        clock: Optional[ManualClock] = None,
    ) -> None:
        config.validate()
        self._config = config
        self._loss = loss_model or NoLoss()
        self._rtt = rtt_profile or ConstantRtt(100.0)
        self._clock = clock or ManualClock()

    def run(self) -> List[WindowSample]:
        cfg = self._config
        cwnd = float(cfg.init_cwnd)
        ssthresh = float(cfg.init_ssthresh)
        samples: List[WindowSample] = []

        for flight_index in range(cfg.flights):
            t_send = self._clock.now()
            rtt = self._rtt.rtt_ms(flight_index)
            if rtt <= 0:
                raise ValueError("rtt must be positive")
            cwnd_before = cwnd
            ssthresh_before = ssthresh
            flight_size = max(1, int(cwnd))
            lost = len(self._loss.lost_positions(flight_index, flight_size))
            event = "ok"

            if lost >= 2:
                ssthresh = max(cwnd / 2.0, MIN_SSTHRESH)
                cwnd = RTO_CWND
                event = "timeout"
            elif lost == 1:
                ssthresh = max(cwnd / 2.0, MIN_SSTHRESH)
                cwnd = max(ssthresh, MIN_FAST_RECOVERY_CWND)
                event = "fast_retransmit"
            elif cwnd < ssthresh:
                cwnd = min(cwnd * 2.0, ssthresh)
                event = "slow_start"
            else:
                cwnd = cwnd + 1.0
                event = "cong_avoid"

            if cfg.max_cwnd is not None:
                cwnd = min(cwnd, float(cfg.max_cwnd))

            self._clock.advance(rtt)
            t_ack = self._clock.now()
            samples.append(
                WindowSample(
                    flight=flight_index,
                    t_send_ms=t_send,
                    rtt_ms=rtt,
                    cwnd=cwnd_before,
                    ssthresh=ssthresh_before,
                    flight_size=flight_size,
                    lost=lost,
                    event=event,
                    cwnd_after=cwnd,
                    t_ack_ms=t_ack,
                )
            )

        return samples


def simulate(
    flights: int,
    *,
    loss_model: Optional[LossModel] = None,
    rtt_profile: Optional[RttProfile] = None,
    init_cwnd: float = 1.0,
    init_ssthresh: float = 64.0,
    max_cwnd: Optional[float] = None,
    clock: Optional[ManualClock] = None,
) -> List[WindowSample]:
    config = SimConfig(
        flights=flights,
        init_cwnd=init_cwnd,
        init_ssthresh=init_ssthresh,
        max_cwnd=max_cwnd,
    )
    return Simulator(config, loss_model, rtt_profile, clock).run()
