"""rng_quality: 随机数输出质量评估库（仅标准库）。

提供四类检查：
  1. 分布均匀性（字节值卡方检验）
  2. 位平衡（总体 0/1 比例 + 逐位位置比例）
  3. 游程检验（位序列游程数）
  4. 周期性（位级 / 字节级自相关扫描）

用法::

    from rng_quality import assess
    report = assess(data)
    print(report["overall"])          # PASS / FAIL / SKIP
    for r in report["results"]:
        print(r["check"], r["status"], r["detail"])
"""

from .core import (
    MIN_BYTES,
    UNIFORMITY_BINS,
    UNIFORMITY_CRIT,
    BALANCE_Z_CRIT,
    RUNS_Z_CRIT,
    PERIODICITY_Z_CRIT,
    BIT_AUTOCORR_MAX_LAG,
    BYTE_AUTOCORR_MAX_LAG,
    assess,
    bit_balance_test,
    periodicity_test,
    runs_test,
    uniformity_test,
)

__all__ = [
    "MIN_BYTES",
    "UNIFORMITY_BINS",
    "UNIFORMITY_CRIT",
    "BALANCE_Z_CRIT",
    "RUNS_Z_CRIT",
    "PERIODICITY_Z_CRIT",
    "BIT_AUTOCORR_MAX_LAG",
    "BYTE_AUTOCORR_MAX_LAG",
    "assess",
    "bit_balance_test",
    "periodicity_test",
    "runs_test",
    "uniformity_test",
]

__version__ = "1.0.0"
