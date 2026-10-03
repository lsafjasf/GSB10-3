"""TCP-Reno style congestion-window state machine.

Units are whole MSS (maximum segment size). cwnd and ssthresh are always
integers, which avoids floating-point drift and keeps two identical runs
bit-for-bit equal.

Growth rules (per acknowledged round-trip):
  * slow start (cwnd < ssthresh): cwnd grows by 1 MSS per MSS acknowledged,
    modeled as one doubling per RTT; growth is capped so cwnd does not jump
    past ssthresh.
  * congestion avoidance (cwnd >= ssthresh): cwnd grows by 1 MSS per RTT.

Back-off rules:
  * three duplicate ACKs / fast retransmit (RFC 5681):
        ssthresh = max(cwnd / 2, 2), cwnd = ssthresh.
  * timeout / RTO (RFC 5681 3.1):
        ssthresh = max(cwnd / 2, 2), cwnd = 1.
"""

from enum import Enum


class Phase(Enum):
    SLOW_START = "slow_start"
    CONGESTION_AVOIDANCE = "congestion_avoidance"


class CongestionSender:
    def __init__(self, initial_cwnd=1, ssthresh=None, mss_bytes=1500,
                 min_cwnd=1, min_ssthresh=2):
        if initial_cwnd < min_cwnd:
            raise ValueError("initial_cwnd must be >= %d" % min_cwnd)
        if mss_bytes <= 0:
            raise ValueError("mss_bytes must be positive")
        if ssthresh is not None and ssthresh < 1:
            raise ValueError("ssthresh must be a positive integer or None")
        self.min_cwnd = min_cwnd
        self.min_ssthresh = min_ssthresh
        self.cwnd = int(initial_cwnd)
        self.ssthresh = None if ssthresh is None else int(ssthresh)
        self.mss_bytes = int(mss_bytes)

    def phase(self):
        if self.ssthresh is None or self.cwnd < self.ssthresh:
            return Phase.SLOW_START
        return Phase.CONGESTION_AVOIDANCE

    def on_round_acked(self):
        """Advance one RTT with every segment acknowledged."""
        if self.phase() is Phase.SLOW_START:
            if self.ssthresh is None:
                self.cwnd += self.cwnd
            else:
                self.cwnd = min(self.cwnd * 2, self.ssthresh)
        else:
            self.cwnd += 1
        return self.cwnd

    def on_triple_dupack(self):
        """Fast retransmit: multiplicative decrease, stay/enter CA."""
        self.ssthresh = max(self.cwnd // 2, self.min_ssthresh)
        self.cwnd = self.ssthresh
        return self.cwnd

    def on_timeout(self):
        """RTO expiry: multiplicative decrease threshold, cwnd resets."""
        self.ssthresh = max(self.cwnd // 2, self.min_ssthresh)
        self.cwnd = self.min_cwnd
        return self.cwnd
