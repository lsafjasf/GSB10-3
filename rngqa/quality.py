"""
rngqa.quality — 随机数质量评估（仅依赖 Python 标准库）。

四类检查：
  1. 分布均匀性  uniformity   : 按字节分 256 桶做卡方拟合优度检验
  2. 位平衡      bit_balance  : 逐比特位 + 全局 1 的占比，z 检验
  3. 游程        runs         : 比特序列中游程数与最长游程，z 检验
  4. 周期性      periodicity  : 自相关 z 检验 + 直接周期搜索

所有统计量均由纯 Python 实现（math / z 分布的双侧尾概率用 math.erf 计算），
评估过程本身不使用任何随机源。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 判据阈值（双侧 z 检验统一用 4 sigma；卡方用 p 值；周期单独判定）
# ---------------------------------------------------------------------------

Z_CRITICAL = 4.0          # |z| > 4  => FAIL，否则 PASS
CHI2_P_MIN = 1e-3         # 卡方 p < 0.001 => FAIL
LAGS = (1, 2, 3, 4, 5, 7, 8, 16, 32, 64, 128, 256, 512, 1024)
MAX_PERIOD_SEARCH = 4096  # 周期搜索最大 lag
MIN_BYTES = 1024          # 少于该长度判定为样本不足
MAX_RUN_LIMIT = 34        # 最长游程超过该值直接 FAIL（NIST 思路的简化）


def _two_sided_p(z: float) -> float:
    """标准正态双侧尾概率：P(|Z| >= |z|)。"""
    return math.erfc(abs(z) / math.sqrt(2.0))


def _z_verdict(z: float) -> str:
    return "FAIL" if abs(z) > Z_CRITICAL else "PASS"


@dataclass
class CheckResult:
    name: str
    verdict: str                 # PASS / FAIL / SKIP
    statistic: str               # 可打印的统计量摘要
    detail: dict = field(default_factory=dict)
    reason: str = ""


# ---------------------------------------------------------------------------
# 1. 分布均匀性：字节取值 0..255 各桶期望 n/256，卡方统计量
#    X2 = sum((obs_i - exp)^2 / exp)，自由度 255
# ---------------------------------------------------------------------------

def chi2_sf_pvalue(x2: float, df: int) -> float:
    """卡方分布上尾概率 P(X2 >= x2)，正则化下不完全伽马 Q(df/2, x2/2)。"""
    if x2 <= 0.0:
        return 1.0
    a = df / 2.0
    x = x2 / 2.0
    if x < a + 1.0:
        # 下不完全伽马级数 P(a,x)，返回 1 - P
        term = 1.0 / a
        total = term
        for k in range(1, 1000):
            term *= x / (a + k)
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        p_lower = total * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return max(0.0, min(1.0, 1.0 - p_lower))
    # 上不完全伽马连分式 Q(a,x)
    b = x + 1.0 - a
    c = 1e300
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    q = h * math.exp(-x + a * math.log(x) - math.lgamma(a))
    return max(0.0, min(1.0, q))


def check_uniformity(data: bytes) -> CheckResult:
    n = len(data)
    if n < MIN_BYTES:
        return CheckResult("uniformity", "SKIP", "", reason=f"n={n} < {MIN_BYTES}")
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    expected = n / 256.0
    x2 = sum((c - expected) ** 2 / expected for c in counts)
    p = chi2_sf_pvalue(x2, 255)
    verdict = "FAIL" if p < CHI2_P_MIN else "PASS"
    return CheckResult(
        "uniformity", verdict,
        f"chi2={x2:.2f} df=255 p={p:.4g} (256 buckets, expect {expected:.1f}/bucket)",
        {"chi2": x2, "df": 255, "p": p},
        reason="p < 0.001" if verdict == "FAIL" else "",
    )


# ---------------------------------------------------------------------------
# 2. 位平衡：N 个比特中 1 的个数应为 N/2（逐位 + 全局）
#    z = (ones - N/2) / sqrt(N/4) = (2*ones - N)/sqrt(N)
# ---------------------------------------------------------------------------

def check_bit_balance(data: bytes) -> CheckResult:
    n = len(data)
    if n < MIN_BYTES:
        return CheckResult("bit_balance", "SKIP", "", reason=f"n={n} < {MIN_BYTES}")
    nbits = n * 8
    ones_total = sum(b.bit_count() for b in data)
    z = (2 * ones_total - nbits) / math.sqrt(nbits)
    worst_pos, worst_z = 0, 0.0
    per_position = []
    for pos in range(8):
        ones = sum((b >> pos) & 1 for b in data)
        zp = (2 * ones - n) / math.sqrt(n)
        per_position.append(zp)
        if abs(zp) > abs(worst_z):
            worst_pos, worst_z = pos, zp
    verdict = "FAIL" if (abs(z) > Z_CRITICAL or abs(worst_z) > Z_CRITICAL) else "PASS"
    reason = ""
    if abs(z) > Z_CRITICAL:
        reason = f"global |z|={abs(z):.1f}"
    elif abs(worst_z) > Z_CRITICAL:
        reason = f"bit position {worst_pos} |z|={abs(worst_z):.1f} (fixed/stuck bit)"
    return CheckResult(
        "bit_balance", verdict,
        f"global ones={ones_total}/{nbits} z={z:.2f}; "
        f"worst bit pos={worst_pos} z={worst_z:.2f}",
        {"z_global": z, "z_per_bit": per_position,
         "worst_position": worst_pos, "z_worst": worst_z},
        reason=reason,
    )


# ---------------------------------------------------------------------------
# 3. 游程：比特序列中的相邻翻转次数 R（01/10 边界）。
#    H0 下 E[R] = (N-1)/2，Var(R) = (N+1)/16... 采用经典公式：
#    E = 2 n1 n0 / N + 1, Var = 2 n1 n0 (2 n1 n0 - N) / (N^2 (N-1))
#    另查最长游程。
# ---------------------------------------------------------------------------

def _transitions(data: bytes) -> int:
    """比特流中相邻比特不同的次数（含字节内与字节边界）。"""
    trans = 0
    prev_last = None
    for b in data:
        x = (b ^ (b << 1)) & 0x7F   # 字节内低 7 位：第 k 位置 1 表示 b[k]!=b[k+1]
        trans += x.bit_count()
        first = b >> 7
        if prev_last is not None and first != prev_last:
            trans += 1
        prev_last = b & 1
    return trans


def _longest_bit_run(data: bytes) -> int:
    longest = cur = 0
    prev = -1
    for b in data:
        for pos in range(7, -1, -1):
            bit = (b >> pos) & 1
            if bit == prev:
                cur += 1
            else:
                longest = max(longest, cur)
                cur, prev = 1, bit
    return max(longest, cur)


def check_runs(data: bytes) -> CheckResult:
    n = len(data)
    if n < MIN_BYTES:
        return CheckResult("runs", "SKIP", "", reason=f"n={n} < {MIN_BYTES}")
    nbits = n * 8
    ones = sum(b.bit_count() for b in data)
    n1, n0 = ones, nbits - ones
    transitions = _transitions(data)
    runs = transitions + 1
    if n1 == 0 or n0 == 0:
        return CheckResult(
            "runs", "FAIL",
            f"runs={runs} (constant bits: {'all 0' if n1 == 0 else 'all 1'})",
            {"runs": runs, "z": None, "longest_run": nbits},
            reason="constant bit sequence, 0 transitions",
        )
    expected = 2.0 * n1 * n0 / nbits + 1.0
    var = (2.0 * n1 * n0 * (2.0 * n1 * n0 - nbits)
           / (nbits * nbits * (nbits - 1)))
    z = (runs - expected) / math.sqrt(var)
    longest = _longest_bit_run(data)
    fails = []
    if abs(z) > Z_CRITICAL:
        fails.append(f"runs |z|={abs(z):.1f}")
    if longest > MAX_RUN_LIMIT:
        fails.append(f"longest run {longest} > {MAX_RUN_LIMIT}")
    verdict = "FAIL" if fails else "PASS"
    return CheckResult(
        "runs", verdict,
        f"runs={runs} expect~{expected:.0f} z={z:.2f}; longest_run={longest}",
        {"runs": runs, "expected": expected, "z": z, "longest_run": longest},
        reason="; ".join(fails),
    )


# ---------------------------------------------------------------------------
# 4. 周期性：比特自相关 + 直接周期搜索
#    lag=k 时取两个错开 k 比特的序列 X, Y（长度 m=N-k），
#    令 d_i = x_i XOR y_i；E[d] = 0.5 时无相关：
#    z = (S - m/2) / sqrt(m)/2   （XOR 后 Bernoulli(0.5)）
#    另直接搜索最小 p，使 data[0:N-p] == data[p:N]（周期完全成立）。
# ---------------------------------------------------------------------------

def _xor_match_count(data: bytes, lag_bits: int) -> tuple[int, int]:
    """返回 (相等比特对数, 总比较对数 m)，比较 bit i 与 bit i+lag_bits。"""
    byte_lag, bit_off = divmod(lag_bits, 8)
    m = (len(data) - byte_lag) * 8 - bit_off
    equal = 0
    if bit_off == 0:
        for i in range(len(data) - byte_lag):
            equal += 8 - (data[i] ^ data[i + byte_lag]).bit_count()
    else:
        for i in range(len(data) - byte_lag - 1):
            shifted = (data[i + byte_lag] << bit_off
                       | data[i + byte_lag + 1] >> (8 - bit_off)) & 0xFF
            equal += 8 - (data[i] ^ shifted).bit_count()
    return equal, m


def find_exact_period(data: bytes, max_lag: int = MAX_PERIOD_SEARCH) -> Optional[int]:
    """最小字节周期 p（1 <= p <= cap），使整个序列严格重复；找不到返回 None。"""
    n = len(data)
    cap = min(max_lag, n - 1)
    for p in range(1, cap + 1):
        if data[: n - p] == data[p:]:
            return p
    return None


def check_periodicity(data: bytes, lags=LAGS) -> CheckResult:
    n = len(data)
    if n < MIN_BYTES:
        return CheckResult("periodicity", "SKIP", "", reason=f"n={n} < {MIN_BYTES}")
    z_by_lag = {}
    worst_lag, worst_z = None, 0.0
    for lag in lags:
        if lag >= n * 8:
            continue
        equal, m = _xor_match_count(data, lag)
        z = (2 * equal - m) / math.sqrt(m)
        z_by_lag[lag] = z
        if abs(z) > abs(worst_z):
            worst_lag, worst_z = lag, z
    period = find_exact_period(data)
    fails = []
    if abs(worst_z) > Z_CRITICAL:
        fails.append(f"autocorr lag={worst_lag} |z|={abs(worst_z):.1f}")
    if period is not None:
        fails.append(f"exact byte period p={period}")
    verdict = "FAIL" if fails else "PASS"
    notable = ", ".join(f"lag{k}:z={z_by_lag[k]:.1f}"
                        for k in lags if k in z_by_lag and abs(z_by_lag[k]) > 3.0)
    stat = f"worst lag={worst_lag} z={worst_z:.2f}; exact_period={period}"
    if notable:
        stat += f"; notable [{notable}]"
    return CheckResult(
        "periodicity", verdict, stat,
        {"z_by_lag": z_by_lag, "worst_lag": worst_lag,
         "z_worst": worst_z, "exact_period": period},
        reason="; ".join(fails),
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

CHECKS = (check_uniformity, check_bit_balance, check_runs, check_periodicity)


@dataclass
class Report:
    sample: str
    n_bytes: int
    results: list[CheckResult]

    @property
    def verdict(self) -> str:
        verdicts = {r.verdict for r in self.results}
        if "FAIL" in verdicts:
            return "FAIL"
        if verdicts <= {"SKIP"}:
            return "SKIP"
        return "PASS"


def assess(data: bytes, sample_name: str = "sample") -> Report:
    return Report(sample_name, len(data), [fn(data) for fn in CHECKS])
