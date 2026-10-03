"""Deterministic TCP-Reno-style congestion window simulator.

Pure standard library. All time comes from an injected ManualClock, so a
given configuration and loss pattern always produces identical results.
"""

from .clock import ManualClock
from .loss import LossModel, NoLoss, ScriptLoss, RandomLoss
from .rtt import RttProfile, ConstantRtt, StepRtt
from .simulator import Simulator, SimConfig, WindowSample, simulate
from .export import samples_to_csv, samples_to_table, write_csv

__all__ = [
    "ManualClock",
    "LossModel",
    "NoLoss",
    "ScriptLoss",
    "RandomLoss",
    "RttProfile",
    "ConstantRtt",
    "StepRtt",
    "Simulator",
    "SimConfig",
    "WindowSample",
    "simulate",
    "samples_to_csv",
    "samples_to_table",
    "write_csv",
]
