"""flowagg: 流记录聚合库（仅标准库）。"""

from .aggregator import FlowAggregator, FlowRecord, FlowSession, interval_gap
from .reference import aggregate_reference, sessions_to_canonical

__version__ = "0.1.0"
__all__ = [
    "FlowAggregator",
    "FlowRecord",
    "FlowSession",
    "interval_gap",
    "aggregate_reference",
    "sessions_to_canonical",
]
