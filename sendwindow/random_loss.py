"""Seedable independent random-loss policy.

Uses :mod:`random.Random` with an explicit seed. No global RNG state is
touched, so the policy is replay-identical for a given seed.
"""

import random


class IndependentLossPolicy:
    """Each transmitted MSS is lost independently with ``loss_rate``."""

    def __init__(self, loss_rate, seed, mss_burst=None):
        if not 0.0 <= loss_rate <= 1.0:
            raise ValueError("loss_rate must be within [0, 1]")
        self.loss_rate = float(loss_rate)
        self.seed = seed
        self.mss_burst = mss_burst
        self._rng = random.Random(seed)

    def __call__(self, round_index, cwnd):
        burst = cwnd if self.mss_burst is None else min(cwnd, self.mss_burst)
        lost = sum(1 for _ in range(max(burst, 0))
                   if self._rng.random() < self.loss_rate)
        return {"lost": lost}
