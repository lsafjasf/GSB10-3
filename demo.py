"""容量趋势外推与告警：样例演示（运行：python3 demo.py）。

输出：
  1. 四类核心情形（平稳 / 单调增长 / 强周期 / 数据点过少）
  2. 波动过大拒绝、已超阈值、下降趋势等边界用例
  3. 每个成功样例的拟合误差数据（重构值、残差、RMSE/MAE/R^2）
  4. 预计达阈时间与置信区间、告警级别
"""

import math
import random

from capacity_forecast import SECONDS_PER_DAY as DAY, alert_level, forecast

T0 = 1_700_000_000.0


def daily_ts(n):
    return [T0 + i * DAY for i in range(n)]


def show_fit_errors(rep, timestamps, values, head=3, tail=3):
    """打印拟合误差数据（首尾各若干点，避免刷屏）。"""
    m = rep.metrics
    print(f"  拟合误差数据：R^2={m.r2:.4f}  RMSE={m.rmse:.4f}  "
          f"MAE={m.mae:.4f}  残差std={m.residual_std:.4f}")
    n = len(values)
    idx = list(range(head)) + list(range(n - tail, n))
    print("    天序号   实际值     重构值     残差")
    shown = set()
    for i in idx:
        if i in shown or i < 0:
            continue
        shown.add(i)
        print(f"    {i:6d}  {values[i]:9.3f}  {m.fitted[i]:9.3f}  "
              f"{m.residuals[i]:+9.3f}")
    print(f"    （共 {n} 点，此处仅展示首尾；完整数组见 report.metrics）")


def run_case(name, ts, vals, threshold, **kwargs):
    print("=" * 72)
    print(f"场景：{name}（样本数={len(vals)}，阈值={threshold:g}）")
    rep = forecast(ts, vals, threshold, **kwargs)
    if not rep.ok:
        print(f"  拒绝预测：{rep.reason}")
        print(f"  告警：{alert_level(rep)['message']}")
        return rep
    period = f"{rep.period_days:.2f} 天" if rep.period_days else "无（未检测到显著周期）"
    print(f"  检测周期：{period}    趋势斜率：{rep.slope_per_day:+.4f}/天")
    show_fit_errors(rep, ts, vals)
    if rep.crossing_time is None:
        print(f"  预计达阈：视界 {rep.horizon_days:.0f} 天内不会达到阈值")
    else:
        d = (rep.crossing_time - rep.last_time) / DAY
        de = (rep.crossing_earliest - rep.last_time) / DAY
        dl = (rep.crossing_latest - rep.last_time) / DAY
        print(f"  预计达阈：{d:.1f} 天后  "
              f"95% 置信区间 [{de:.1f}, {dl:.1f}] 天")
    print(f"  告警：{alert_level(rep)['level'].upper()} — {alert_level(rep)['message']}")
    return rep


def main():
    rng = random.Random(2026)

    # 1) 平稳数据：40 上下微小抖动，阈值 90
    ts = daily_ts(60)
    run_case("平稳数据（无增长）", ts,
             [40.0 + rng.uniform(-0.4, 0.4) for _ in ts], threshold=90.0)

    # 2) 单调增长：y=10+0.5t，阈值 80（真值第 140 天）
    ts = daily_ts(60)
    run_case("单调增长", ts,
             [10.0 + 0.5 * i for i in range(60)], threshold=80.0)

    # 3) 强周期 + 缓慢增长（7 天周期，波峰当前约 58，阈值 60）
    ts = daily_ts(60)
    vals = [40.0 + 0.05 * i + 15.0 * math.sin(2 * math.pi * i / 7.0)
            + rng.gauss(0, 0.2) for i in range(60)]
    run_case("强周期+缓慢增长（线性外推会在波峰误报）", ts, vals, threshold=60.0)

    # 4) 数据点过少
    run_case("数据点过少（5 个点）", daily_ts(5),
             [1.0, 2.0, 3.0, 4.0, 5.0], threshold=100.0)

    # 5) 波动过大：纯随机
    ts = daily_ts(80)
    run_case("剧烈随机波动", ts,
             [rng.uniform(0, 100) for _ in ts], threshold=120.0)

    # 6) 边界：当前已超阈值
    ts = daily_ts(30)
    run_case("边界：当前已超阈值", ts,
             [95.0 + 0.1 * i for i in range(30)], threshold=90.0)

    # 7) 边界：使用量下降
    ts = daily_ts(40)
    run_case("边界：使用量下降", ts,
             [70.0 - 0.5 * i for i in range(40)], threshold=90.0)


if __name__ == "__main__":
    main()
