"""核心检查实现。仅使用 Python 标准库。

判据总览（常量即判据，修改需谨慎）：

* 分布均匀性 uniformity_test
  - 把样本按字节取值分入 256 个桶，期望每桶 n/256。
  - 统计量 chi2 = sum((obs-exp)^2/exp)，自由度 255。
  - 判据：chi2 <= UNIFORMITY_CRIT(=310.0) 判 PASS，否则 FAIL。
    310.0 约为卡方(255) 分布 0.987 分位点，即显著性约 1%。
    对全零等极端输出 chi2 = 255*n，必然 FAIL。

* 位平衡 bit_balance_test
  - 总体：ones 为 1 的位数，z = (ones - N/2) / sqrt(N/4)，N=8n。
  - 逐位：对每个位位置（bit0..bit7）单独计算同样的 z。
  - 判据：总体 |z| <= BALANCE_Z_CRIT(=4) 且每个位位置 |z| <= 4 判 PASS。
    |z|>4 对应正态双侧 p 约 6.3e-5，误报率极低；
    “低位固定”类缺陷会在逐位检查上产生巨大 |z|，必然 FAIL。

* 游程检验 runs_test
  - 位序列中相邻位不同的次数 R（游程数-1）。
  - 期望 ER = (N-1)/2，方差 VarR = (N-1)/4，z = (R-ER)/sqrt(VarR)。
  - 判据：|z| <= RUNS_Z_CRIT(=4) 判 PASS。
    全零 / 全同输出 R=0，z 约 -sqrt(N)，必然 FAIL；
    交替位等过规则序列 R 过大，同样 FAIL。

* 周期性 periodicity_test
  - 位级自相关：lag = 1..BIT_AUTOCORR_MAX_LAG(=64) 位，
    把位映射为 +1/-1，z = sum(x_i * x_{i+lag}) / sqrt(N-lag)。
  - 字节级自相关：lag = 1..BYTE_AUTOCORR_MAX_LAG(=128) 字节，
    把字节映射为 (b-127.5)/128，z = sum(x_i*x_{i+lag}) / sqrt((n-lag)*Var)，
    Var = (256^2-1)/12 为均匀字节的方差。
  - 判据：所有 lag 的 |z| <= PERIODICITY_Z_CRIT(=5) 判 PASS。
    阈值取 5 是为多重比较留余量（共扫描 192 个 lag，
    单次误报 p 约 5.7e-7，整体误报率约 1e-4）。
    周期为 P（位或字节）的序列在 lag=P 处 z 接近 sqrt(N)，必然 FAIL。
  - 附加输出 candidate_period：正相关最强（z 最大）的 lag，
    供定位周期参考，不参与判定。

所有检查在样本不足 MIN_BYTES(=2048) 字节时返回 SKIP，
避免小样本上的统计量失真造成误判。
"""

import math

MIN_BYTES = 2048

UNIFORMITY_BINS = 256
UNIFORMITY_CRIT = 310.0

BALANCE_Z_CRIT = 4.0
RUNS_Z_CRIT = 4.0

PERIODICITY_Z_CRIT = 5.0
BIT_AUTOCORR_MAX_LAG = 64
BYTE_AUTOCORR_MAX_LAG = 128

_STATUS_PASS = "PASS"
_STATUS_FAIL = "FAIL"
_STATUS_SKIP = "SKIP"


def _to_bits(data):
    """字节串 -> 0/1 位列表（每字节 MSB 在前）。"""
    bits = []
    append = bits.append
    for byte in data:
        for shift in range(7, -1, -1):
            append((byte >> shift) & 1)
    return bits


def _result(check, status, detail, stats):
    return {
        "check": check,
        "status": status,
        "detail": detail,
        "stats": stats,
    }


def _skip(check, n):
    return _result(
        check,
        _STATUS_SKIP,
        "样本不足：%d 字节 < 最少 %d 字节，无法给出可靠结论" % (n, MIN_BYTES),
        {"n_bytes": n},
    )


def gammaincc(a, x):
    """正则化上不完全伽马函数 Q(a, x) = Gamma(a, x) / Gamma(a)。

    仅用于报告卡方检验的 p 值，不影响判定（判定用固定临界值）。
    级数/连分式实现，收敛后停止。
    """
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        # 级数展开求 P(a, x)，返回 1 - P
        term = 1.0 / a
        total = term
        for k in range(1, 1000):
            term *= x / (a + k)
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        p = total * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return max(0.0, min(1.0, 1.0 - p))
    # 连分式求 Q(a, x)
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / max(b, tiny)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    q = h * math.exp(-x + a * math.log(x) - math.lgamma(a))
    return max(0.0, min(1.0, q))


def uniformity_test(data):
    """分布均匀性：字节值 256 桶卡方检验。"""
    n = len(data)
    if n < MIN_BYTES:
        return _skip("uniformity", n)
    counts = [0] * UNIFORMITY_BINS
    for byte in data:
        counts[byte] += 1
    expected = n / UNIFORMITY_BINS
    chi2 = 0.0
    for obs in counts:
        diff = obs - expected
        chi2 += diff * diff / expected
    df = UNIFORMITY_BINS - 1
    p_value = gammaincc(df / 2.0, chi2 / 2.0)
    status = _STATUS_PASS if chi2 <= UNIFORMITY_CRIT else _STATUS_FAIL
    p_text = "<1e-300" if p_value < 1e-300 else ("%.4g" % p_value)
    detail = (
        "卡方=%.2f，临界值=%.1f（df=%d，p=%s）；"
        "卡方超过临界值说明字节取值分布显著偏离均匀" % (chi2, UNIFORMITY_CRIT, df, p_text)
    )
    return _result(
        "uniformity",
        status,
        detail,
        {"n_bytes": n, "chi2": chi2, "df": df, "crit": UNIFORMITY_CRIT, "p_value": p_value},
    )


def bit_balance_test(data):
    """位平衡：总体 0/1 比例 + 逐位位置比例，正态近似 z 检验。"""
    n = len(data)
    if n < MIN_BYTES:
        return _skip("bit_balance", n)
    total_bits = 8 * n
    per_pos_ones = [0] * 8
    total_ones = 0
    for byte in data:
        for shift in range(8):
            per_pos_ones[shift] += (byte >> shift) & 1
        total_ones += bin(byte).count("1")
    z_overall = (total_ones - total_bits / 2.0) / math.sqrt(total_bits / 4.0)
    per_pos_z = []
    for shift in range(8):
        z = (per_pos_ones[shift] - n / 2.0) / math.sqrt(n / 4.0)
        per_pos_z.append(z)
    max_pos_z = max(abs(z) for z in per_pos_z)
    ok = abs(z_overall) <= BALANCE_Z_CRIT and max_pos_z <= BALANCE_Z_CRIT
    status = _STATUS_PASS if ok else _STATUS_FAIL
    worst = max(range(8), key=lambda s: abs(per_pos_z[s]))
    detail = (
        "总体 |z|=%.2f，逐位最大 |z|=%.2f（bit%d），临界值=%.1f；"
        "某一位长期偏向 0 或 1（如低位固定）会使逐位 z 极大" % (
            abs(z_overall), max_pos_z, worst, BALANCE_Z_CRIT)
    )
    return _result(
        "bit_balance",
        status,
        detail,
        {
            "n_bytes": n,
            "total_bits": total_bits,
            "ones": total_ones,
            "z_overall": z_overall,
            "per_position_z": per_pos_z,
            "crit": BALANCE_Z_CRIT,
        },
    )


def runs_test(data):
    """游程检验：位序列游程数的正态近似 z 检验。"""
    n = len(data)
    if n < MIN_BYTES:
        return _skip("runs", n)
    bits = _to_bits(data)
    total_bits = len(bits)
    runs = 1
    for i in range(1, total_bits):
        if bits[i] != bits[i - 1]:
            runs += 1
    expected = (total_bits - 1) / 2.0 + 1.0
    var = (total_bits - 1) / 4.0
    z = (runs - expected) / math.sqrt(var)
    status = _STATUS_PASS if abs(z) <= RUNS_Z_CRIT else _STATUS_FAIL
    detail = (
        "游程数=%d，期望≈%.1f，|z|=%.2f，临界值=%.1f；"
        "游程过少（如全零）或过多（如交替位）都会 FAIL" % (
            runs, expected, abs(z), RUNS_Z_CRIT)
    )
    return _result(
        "runs",
        status,
        detail,
        {"n_bytes": n, "total_bits": total_bits, "runs": runs, "expected": expected, "z": z, "crit": RUNS_Z_CRIT},
    )


def periodicity_test(data):
    """周期性：位级（lag 1..64）与字节级（lag 1..128）自相关扫描。"""
    n = len(data)
    if n < MIN_BYTES:
        return _skip("periodicity", n)

    # 位级自相关：位映射为 +1/-1
    bits = _to_bits(data)
    total_bits = len(bits)
    signs = [1 if b else -1 for b in bits]
    bit_lag_z = []
    for lag in range(1, BIT_AUTOCORR_MAX_LAG + 1):
        m = total_bits - lag
        s = 0
        for i in range(m):
            s += signs[i] * signs[i + lag]
        bit_lag_z.append(s / math.sqrt(m))

    # 字节级自相关：字节映射为 (b-127.5)/128，除以均匀方差
    var_uniform = (256.0 * 256.0 - 1.0) / 12.0
    centered = [(b - 127.5) / 128.0 for b in data]
    byte_lag_z = []
    for lag in range(1, BYTE_AUTOCORR_MAX_LAG + 1):
        m = n - lag
        s = 0.0
        for i in range(m):
            s += centered[i] * centered[i + lag]
        byte_lag_z.append(s / math.sqrt(m * var_uniform / (128.0 * 128.0)))

    max_bit_z = max(abs(z) for z in bit_lag_z)
    max_byte_z = max(abs(z) for z in byte_lag_z)
    max_z = max(max_bit_z, max_byte_z)
    status = _STATUS_PASS if max_z <= PERIODICITY_Z_CRIT else _STATUS_FAIL

    # 候选周期（参考信息，不参与判定）：各扫描中最大正相关的 lag，
    # 仅当其 z 超过临界值时列出；真实周期处自相关接近 +1，通常对应最大峰。
    def strongest_positive(z_list):
        best_idx, best_z = None, PERIODICITY_Z_CRIT
        for idx, z in enumerate(z_list):
            if z > best_z:
                best_idx, best_z = idx, z
        if best_idx is None:
            return None
        return best_idx + 1, best_z

    bit_cand = strongest_positive(bit_lag_z)
    byte_cand = strongest_positive(byte_lag_z)

    def fmt_cand(cand, unit, multiplier):
        if cand is None:
            return "无"
        return "%s lag=%d（z=%.2f，约 %d 位）" % (unit, cand[0], cand[1], cand[0] * multiplier)

    detail = (
        "位级最大 |z|=%.2f，字节级最大 |z|=%.2f，临界值=%.1f；"
        "候选周期（参考）：位级 %s；字节级 %s" % (
            max_bit_z, max_byte_z, PERIODICITY_Z_CRIT,
            fmt_cand(bit_cand, "bit", 1), fmt_cand(byte_cand, "byte", 8))
    )
    return _result(
        "periodicity",
        status,
        detail,
        {
            "n_bytes": n,
            "bit_lag_z": bit_lag_z,
            "byte_lag_z": byte_lag_z,
            "max_bit_abs_z": max_bit_z,
            "max_byte_abs_z": max_byte_z,
            "candidate_bit_lag": bit_cand[0] if bit_cand else None,
            "candidate_byte_lag": byte_cand[0] if byte_cand else None,
            "crit": PERIODICITY_Z_CRIT,
        },
    )


_CHECKS = (uniformity_test, bit_balance_test, runs_test, periodicity_test)


def assess(data):
    """对样本执行全部四类检查，返回汇总报告（dict）。

    overall 判定规则：
      - 任一检查 FAIL -> overall = FAIL
      - 无 FAIL 且至少一个检查 PASS -> PASS（其余为 SKIP）
      - 全部 SKIP -> SKIP
    """
    data = bytes(data)
    results = [check(data) for check in _CHECKS]
    statuses = [r["status"] for r in results]
    if _STATUS_FAIL in statuses:
        overall = _STATUS_FAIL
    elif _STATUS_PASS in statuses:
        overall = _STATUS_PASS
    else:
        overall = _STATUS_SKIP
    return {
        "n_bytes": len(data),
        "overall": overall,
        "results": results,
    }
