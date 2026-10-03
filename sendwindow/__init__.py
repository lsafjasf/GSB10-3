"""Deterministic TCP-style send-window (cwnd) simulator.

Only an injectable virtual clock is used; no real time source is read.
"""

from .clock import VirtualClock
from .congestion import CongestionSender, Phase
from .lossmodel import FORCE_FAST, FORCE_RTO, LossSpec, build_script, coerce_spec
from .random_loss import IndependentLossPolicy
from .simulator import Simulator, WindowSample
from .report import rows_to_csv, write_csv, sha256_text

__all__ = [
    "VirtualClock",
    "CongestionSender",
    "Phase",
    "LossSpec",
    "FORCE_FAST",
    "FORCE_RTO",
    "build_script",
    "coerce_spec",
    "IndependentLossPolicy",
    "Simulator",
    "WindowSample",
    "rows_to_csv",
    "write_csv",
    "sha256_text",
]
