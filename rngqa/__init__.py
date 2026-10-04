"""rngqa — 标准库实现的随机数质量评估（均匀性/位平衡/游程/周期性）。"""
from .quality import (
    assess, Report, CheckResult,
    check_uniformity, check_bit_balance, check_runs, check_periodicity,
    find_exact_period,
)
__all__ = [
    "assess", "Report", "CheckResult",
    "check_uniformity", "check_bit_balance", "check_runs",
    "check_periodicity", "find_exact_period",
]
