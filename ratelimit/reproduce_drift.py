"""长时间运行漂移复现 / 前后对比脚本。

用模拟时钟在若干流量模式下分别运行有缺陷版和修复版限流器，
统计实际通过量与理论上限的差额（漂移）。

用法: python3 reproduce_drift.py
"""

from fake_clock import FakeClock
from buggy_rate_limiter import BuggyTokenBucket
from rate_limiter import TokenBucket


def simulate(limiter_cls, rate, capacity, interval_s, duration_s, clock_events=None):
    """以固定间隔打请求，返回 (通过数, 请求总数)。

    clock_events: {模拟秒: 回调(clock)}，用于注入时钟回拨等事件。
    """
    clock = FakeClock()
    limiter = limiter_cls(rate=rate, capacity=capacity, clock=clock)
    allowed = 0
    total = 0
    steps = int(duration_s / interval_s)
    events = clock_events or {}
    for i in range(steps):
        t = i * interval_s
        event = events.get(round(t, 6))
        if event is not None:
            event(clock)
        if limiter.allow():
            allowed += 1
        total += 1
        clock.advance(interval_s)
    return allowed, total


def expected_allowed(rate, capacity, duration_s):
    """令牌桶理论上限：初始满桶 + 运行期间补充的令牌。"""
    return capacity + rate * duration_s


def run_scenario(name, rate, capacity, interval_s, duration_s, clock_events=None):
    rows = []
    for label, cls in (("修复前(buggy)", BuggyTokenBucket), ("修复后(fixed)", TokenBucket)):
        allowed, total = simulate(cls, rate, capacity, interval_s, duration_s, clock_events)
        limit = expected_allowed(rate, capacity, duration_s)
        drift = allowed - limit
        rows.append((label, total, allowed, limit, drift, allowed / limit if limit else 0))
    print(f"\n=== {name} ===")
    print(f"配置: rate={rate}/s, capacity={capacity}, 请求间隔={interval_s*1000:g}ms, "
          f"模拟时长={duration_s:g}s")
    print(f"{'版本':<16}{'请求总数':>10}{'实际通过':>12}{'理论上限':>12}{'漂移':>12}{'实际/上限':>10}")
    for label, total, allowed, limit, drift, ratio in rows:
        print(f"{label:<16}{total:>10}{allowed:>12}{limit:>12.0f}{drift:>+12.0f}{ratio:>10.2f}x")
    return rows


def main():
    # 场景 1：高频小间隔（每 1ms 一个请求，速率 100/s）—— 取整漂移最典型
    run_scenario("场景1 高频请求 1ms 间隔", rate=100, capacity=10,
                 interval_s=0.001, duration_s=600)

    # 场景 2：中频请求（每 5ms 一个请求，速率 100/s）
    run_scenario("场景2 中频请求 5ms 间隔", rate=100, capacity=10,
                 interval_s=0.005, duration_s=600)

    # 场景 3：低于限速的持续流量（每 20ms 一个请求，速率 100/s）—— 对照组，应全部通过
    run_scenario("场景3 低于限速 20ms 间隔(对照)", rate=100, capacity=10,
                 interval_s=0.020, duration_s=600)

    # 场景 4：突发流量（瞬时打满，随后每 100ms 一个请求）
    run_scenario("场景4 空闲后突发+持续", rate=100, capacity=10,
                 interval_s=0.1, duration_s=600)

    # 场景 5：长时间运行 + 时钟回拨（模拟 NTP 校时，回拨 300s）
    def rewind(clock):
        clock.set(clock() - 300)

    rows = run_scenario("场景5 长运行+时钟回拨300s", rate=100, capacity=10,
                        interval_s=0.001, duration_s=600,
                        clock_events={100.0: rewind, 400.0: rewind})
    # 回拨后时钟需 300s 才能追平 last_refill，期间限流器可见的前进时间
    # 只有约 100s，因此修复版的期望上限 = 10 + 100*100 = 10010（fail-closed，
    # 安全方向）。修复版实际通过 10009，与可见时间上Limit一致。
    print("说明: 回拨期间时钟需追平 last_refill，限流器可见前进时间约 100s，")
    print("      修复版期望通过 ≈ 10010（fail-closed 为预期行为，不多放、不重复发放）。")


if __name__ == "__main__":
    main()
