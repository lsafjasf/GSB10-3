"""容量趋势外推与告警库（仅依赖 Python 标准库）。

模型：y(t) = c0 + c1*t + sum_k[a_k*cos(2*pi*k*t/P) + b_k*sin(2*pi*k*t/P)]
  - t 以"天"为单位（自首个采样点起算）
  - P 为检测到的主导周期（天），k = 1..harmonics
  - 通过正规方程 + 高斯消元（带微小岭正则）求解最小二乘

能力：
  - 周期分量建模，输出拟合值/残差/RMSE/MAE/R^2 等拟合误差数据
  - 预测达到阈值的时间，并给出基于残差带的置信区间
  - 数据不足或波动过大（拟合不可靠）时拒绝预测并说明依据
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

SECONDS_PER_DAY = 86400.0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class FitMetrics:
    """拟合误差数据（与输入时间序列一一对应，已按时间升序排列）。"""
    r2: float                      # 决定系数，越接近 1 拟合越好
    rmse: float                    # 均方根误差
    mae: float                     # 平均绝对误差
    residual_std: float            # 残差标准差（用于置信区间）
    fitted: List[float]            # 重构值（模型在历史点上的取值）
    residuals: List[float]         # 残差 = 实际值 - 重构值


@dataclass
class ForecastReport:
    """外推结果。ok=False 时 reason 给出拒绝依据。"""
    ok: bool
    reason: str = ""
    threshold: float = 0.0
    period_days: Optional[float] = None      # 检测到的周期（天），无周期分量为 None
    slope_per_day: float = 0.0               # 趋势斜率（单位/天）
    metrics: Optional[FitMetrics] = None
    last_time: float = 0.0                   # 最后一个历史点的时间戳（秒）
    last_value: float = 0.0
    horizon_days: float = 0.0                # 外推视界（天）
    crossing_time: Optional[float] = None    # 预计达到阈值的时间戳（秒），None=视界内不会达到
    crossing_earliest: Optional[float] = None  # 置信下界（最早，悲观）
    crossing_latest: Optional[float] = None    # 置信上界（最晚，乐观）


# ---------------------------------------------------------------------------
# 线性代数（纯标准库）
# ---------------------------------------------------------------------------

def _solve_linear(matrix: List[List[float]], vector: List[float]) -> List[float]:
    """高斯消元 + 部分主元，解 matrix @ x = vector。"""
    n = len(vector)
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ArithmeticError("设计矩阵奇异，无法求解")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        for r in range(col + 1, n):
            factor = aug[r][col] / aug[col][col]
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = aug[r][n] - sum(aug[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / aug[r][r]
    return x


def _least_squares(design: List[List[float]], y: Sequence[float]) -> List[float]:
    """最小二乘；优先精确求解，设计矩阵病态时才回退到岭正则。"""
    p = len(design[0])
    ata = [[0.0] * p for _ in range(p)]
    aty = [0.0] * p
    for row, yi in zip(design, y):
        for i in range(p):
            aty[i] += row[i] * yi
            for j in range(i, p):
                ata[i][j] += row[i] * row[j]
    for i in range(p):
        for j in range(i):
            ata[i][j] = ata[j][i]
    try:
        return _solve_linear([row[:] for row in ata], aty)
    except ArithmeticError:
        ridge = 1e-9 * max(1.0, max(ata[i][i] for i in range(p)))
        for i in range(p):
            ata[i][i] += ridge
        return _solve_linear(ata, aty)


# ---------------------------------------------------------------------------
# 周期检测（去趋势后的自相关）
# ---------------------------------------------------------------------------

def _detect_period(t: Sequence[float], y: Sequence[float],
                   autocorr_threshold: float) -> Optional[float]:
    """对线性去趋势后的残差做自相关，返回主导周期（天）或 None。"""
    n = len(t)
    if n < 6:
        return None
    # 线性去趋势
    design = [[1.0, ti] for ti in t]
    a, b = _least_squares(design, y)
    resid = [yi - (a + b * ti) for ti, yi in zip(t, y)]
    energy = sum(r * r for r in resid)
    if energy < 1e-12:
        return None
    best_lag, best_ac = 0, autocorr_threshold
    for lag in range(2, n // 2 + 1):
        ac = sum(resid[i] * resid[i + lag] for i in range(n - lag)) / energy
        if ac > best_ac:
            best_ac, best_lag = ac, lag
    if best_lag == 0:
        return None
    mean_dt = (t[-1] - t[0]) / (n - 1)
    return best_lag * mean_dt


# ---------------------------------------------------------------------------
# 模型构建
# ---------------------------------------------------------------------------

def _design_row(t: float, period: Optional[float], harmonics: int) -> List[float]:
    row = [1.0, t]
    if period:
        for k in range(1, harmonics + 1):
            w = 2.0 * math.pi * k / period
            row.append(math.cos(w * t))
            row.append(math.sin(w * t))
    return row


def _fit_model(t: Sequence[float], y: Sequence[float],
               period: Optional[float], harmonics: int):
    design = [_design_row(ti, period, harmonics) for ti in t]
    coeffs = _least_squares(design, y)

    def predict(ti: float) -> float:
        return sum(c * b for c, b in zip(coeffs, _design_row(ti, period, harmonics)))

    return coeffs, predict


def _compute_metrics(t: Sequence[float], y: Sequence[float], predict) -> FitMetrics:
    fitted = [predict(ti) for ti in t]
    residuals = [yi - fi for yi, fi in zip(y, fitted)]
    n = len(y)
    ss_res = sum(r * r for r in residuals)
    mean_y = sum(y) / n
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    if ss_tot < 1e-12:
        r2 = 1.0 if ss_res < 1e-12 else 0.0
    else:
        r2 = 1.0 - ss_res / ss_tot
    rmse = math.sqrt(ss_res / n)
    mae = sum(abs(r) for r in residuals) / n
    residual_std = math.sqrt(ss_res / n)
    return FitMetrics(r2=r2, rmse=rmse, mae=mae, residual_std=residual_std,
                      fitted=fitted, residuals=residuals)


# ---------------------------------------------------------------------------
# 阈值交叉时间求解
# ---------------------------------------------------------------------------

def _find_crossing(func, threshold: float, t_start: float, t_end: float,
                   step: float) -> Optional[float]:
    """在 [t_start, t_end] 内找 func(t) 首次 >= threshold 的时刻（二分细化）。"""
    if func(t_start) >= threshold:
        return t_start
    prev_t = t_start
    t = t_start + step
    while t <= t_end:
        if func(t) >= threshold:
            lo, hi = prev_t, t
            for _ in range(60):
                mid = (lo + hi) / 2.0
                if func(mid) >= threshold:
                    hi = mid
                else:
                    lo = mid
            return hi
        prev_t = t
        t += step
    return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def forecast(timestamps: Sequence[float], values: Sequence[float],
             threshold: float,
             min_points: int = 8,
             min_r2: float = 0.6,
             harmonics: int = 2,
             autocorr_threshold: float = 0.45,
             z: float = 1.96,
             horizon_factor: float = 4.0,
             min_horizon_days: float = 7.0) -> ForecastReport:
    """容量趋势外推。

    参数：
        timestamps: 采样时间戳（秒，可乱序，重复点保留最后值）
        values:     容量观测值
        threshold:  告警阈值（容量上限）
        min_points: 最少数据点数，不足则拒绝
        min_r2:     最低可接受 R^2，低于则判定波动过大、拒绝预测
        z:          置信带倍数（1.96 ≈ 95%）
    """
    if len(timestamps) != len(values):
        raise ValueError("timestamps 与 values 长度不一致")

    report = ForecastReport(ok=False, threshold=threshold)

    def reject(reason: str) -> ForecastReport:
        report.ok = False
        report.reason = reason
        return report

    n = len(values)
    if n < min_points:
        return reject(f"数据点不足：{n} 个样本 < 最少要求 {min_points} 个，"
                      f"无法可靠估计趋势与周期")

    # 排序 + 去重（保留最后出现的值）
    pairs = sorted(zip(timestamps, values))
    deduped = []
    for ts, v in pairs:
        if deduped and ts == deduped[-1][0]:
            deduped[-1] = (ts, v)
        else:
            deduped.append((ts, v))
    if len(deduped) < min_points:
        return reject(f"数据点不足：去重后 {len(deduped)} 个样本 < 最少要求 {min_points} 个")
    ts_sorted = [p[0] for p in deduped]
    y = [p[1] for p in deduped]

    t0 = ts_sorted[0]
    t = [(ts - t0) / SECONDS_PER_DAY for ts in ts_sorted]  # 天，从 0 起
    span_days = t[-1] - t[0]
    if span_days <= 0:
        return reject("数据点不足：所有样本时间戳相同，无法估计趋势")

    # 周期检测 + 模型选择（周期模型需显著优于纯线性才采用）
    period = _detect_period(t, y, autocorr_threshold)
    coeffs_lin, predict_lin = _fit_model(t, y, None, harmonics)
    metrics_lin = _compute_metrics(t, y, predict_lin)
    if period is not None:
        coeffs_per, predict_per = _fit_model(t, y, period, harmonics)
        metrics_per = _compute_metrics(t, y, predict_per)
        if metrics_per.r2 >= metrics_lin.r2 + 0.03:
            coeffs, predict, metrics = coeffs_per, predict_per, metrics_per
        else:
            period = None
            coeffs, predict, metrics = coeffs_lin, predict_lin, metrics_lin
    else:
        coeffs, predict, metrics = coeffs_lin, predict_lin, metrics_lin

    if metrics.r2 < min_r2:
        # R^2 低不必然拒绝：平稳低噪数据（近似常数）R^2 也接近 0，
        # 但此时残差相对阈值余量很小，预测依然可靠。
        # 仅当残差相对阈值余量不可忽略时，才判定波动过大。
        headroom = threshold - max(y)
        if headroom <= 0 or metrics.rmse > 0.1 * headroom:
            return reject(
                f"波动过大，拟合不可靠：R^2={metrics.r2:.3f} < 最低要求 {min_r2}，"
                f"且 RMSE={metrics.rmse:.4g} 相对阈值余量 "
                f"{headroom:.4g} 不可忽略，数据噪声淹没趋势/周期，拒绝预测")

    slope = coeffs[1]
    horizon_days = max(span_days * horizon_factor, min_horizon_days)
    t_last = t[-1]
    t_end = t_last + horizon_days
    step = span_days / 200.0
    if period:
        step = min(step, period / 8.0)
    step = max(step, 1e-6)

    band = z * metrics.residual_std
    crossing = _find_crossing(predict, threshold, t_last, t_end, step)
    crossing_early = _find_crossing(lambda ti: predict(ti) + band, threshold,
                                    t_last, t_end, step)
    crossing_late = _find_crossing(lambda ti: predict(ti) - band, threshold,
                                   t_last, t_end, step)

    report.ok = True
    report.period_days = period
    report.slope_per_day = slope
    report.metrics = metrics
    report.last_time = ts_sorted[-1]
    report.last_value = y[-1]
    report.horizon_days = horizon_days
    report.crossing_time = t0 + crossing * SECONDS_PER_DAY if crossing is not None else None
    report.crossing_earliest = t0 + crossing_early * SECONDS_PER_DAY if crossing_early is not None else None
    report.crossing_latest = t0 + crossing_late * SECONDS_PER_DAY if crossing_late is not None else None
    return report


# ---------------------------------------------------------------------------
# 告警分级
# ---------------------------------------------------------------------------

def alert_level(report: ForecastReport,
                warning_days: float = 14.0,
                critical_days: float = 3.0) -> dict:
    """根据预测结果给出告警级别。

    返回 dict：level ∈ exceeded/critical/warning/watch/safe/unknown。
    """
    if not report.ok:
        return {"level": "unknown", "message": f"无法预测：{report.reason}"}

    def days_until(ts: Optional[float]) -> Optional[float]:
        if ts is None:
            return None
        return (ts - report.last_time) / SECONDS_PER_DAY

    d = days_until(report.crossing_time)
    d_early = days_until(report.crossing_earliest)
    d_late = days_until(report.crossing_latest)

    base = {
        "days_to_threshold": d,
        "days_to_threshold_earliest": d_early,
        "days_to_threshold_latest": d_late,
    }
    if report.last_value >= report.threshold or (d is not None and d <= 0):
        return {**base, "level": "exceeded",
                "message": f"当前值 {report.last_value:.4g} 已达到/超过阈值 {report.threshold:.4g}"}
    if d is None:
        return {**base, "level": "safe",
                "message": f"视界 {report.horizon_days:.0f} 天内预计不会达到阈值 "
                           f"{report.threshold:.4g}（趋势 {report.slope_per_day:+.4g}/天）"}
    if d <= critical_days:
        level = "critical"
    elif d <= warning_days:
        level = "warning"
    else:
        level = "watch"
    ci = ""
    if d_early is not None and d_late is not None:
        ci = f"，置信区间 [{d_early:.1f}, {d_late:.1f}] 天"
    elif d_early is not None:
        ci = f"，最早 {d_early:.1f} 天"
    return {**base, "level": level,
            "message": f"预计 {d:.1f} 天后达到阈值 {report.threshold:.4g}{ci}"}
