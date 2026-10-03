"""场景模拟：产出批大小变化数据、响应速度与稳定性数据。

运行：python3 simulate.py
"""
import json
import random

from adaptive_batch import AdaptiveBatchSizer

ROUNDS = 120


def make_sizer(initial_size=100):
    return AdaptiveBatchSizer(
        min_size=10, max_size=500, initial_size=initial_size,
        target_latency=0.5, deadband=0.10,
        failure_threshold=0.20, decrease_factor=0.5,
        increase_ratio=0.10, cooldown_rounds=2,
    )


def run_scenario(name, per_item_fn, failure_rate_fn, seed=42, initial_size=100):
    """per_item_fn(round)->单条消息处理耗时；failure_rate_fn(round)->失败率。"""
    rng = random.Random(seed)
    sizer = make_sizer(initial_size)
    trace = []
    for r in range(ROUNDS):
        n = sizer.size
        per_item = per_item_fn(r)
        duration = n * per_item * rng.uniform(0.97, 1.03)  # 3% 噪声
        failures = int(n * failure_rate_fn(r))
        new_size = sizer.record(duration, failures)
        trace.append({
            "round": r, "size": new_size, "duration": round(duration, 4),
            "failures": failures, "reason": sizer.history[-1].reason,
        })
    return sizer, trace


def rounds_to_recover(trace, target, deadband, change_round):
    """返回 (耗时降回目标上界内轮数, 耗时稳定落入死区轮数)。"""
    upper = target * (1 + deadband)
    lower = target * (1 - deadband)
    under_upper = in_band = None
    for t in trace:
        if t["round"] < change_round:
            continue
        n = t["round"] - change_round + 1
        if under_upper is None and t["duration"] <= upper:
            under_upper = n
        if in_band is None and lower <= t["duration"] <= upper:
            in_band = n
    return under_upper, in_band


def print_trace(trace, every=10):
    print(f"  {'轮次':>4} {'批大小':>6} {'耗时(s)':>8} {'失败数':>5}  原因")
    for t in trace:
        if t["round"] % every == 0 or t["round"] == len(trace) - 1:
            print(f"  {t['round']:>4} {t['size']:>6} {t['duration']:>8.3f} "
                  f"{t['failures']:>5}  {t['reason']}")


def scenario_stable():
    sizer, trace = run_scenario("stable", lambda r: 0.005, lambda r: 0.0, initial_size=30)
    st = sizer.stability(window=60)
    return "稳定负载", trace, st, {}


def scenario_slowdown():
    change = 60
    sizer, trace = run_scenario(
        "slowdown", lambda r: 0.005 if r < change else 0.030, lambda r: 0.0)
    under_upper, in_band = rounds_to_recover(trace, 0.5, 0.10, change)
    st = sizer.stability(window=30)
    # 量化：6 倍变慢，乘性减半理论上 ceil(log2(6))=3 轮即可压回目标上界内
    return "突发变慢(单条耗时 5ms→30ms, 6x)", trace, st, {
        "change_round": change,
        "rounds_until_under_target": under_upper,
        "rounds_until_settled_in_band": in_band,
        "size_at_change": trace[change - 1]["size"],
        "size_after_settle": trace[-1]["size"],
    }


def scenario_persistent_failure():
    sizer, trace = run_scenario(
        "persistent_failure", lambda r: 0.004, lambda r: 0.5)
    st = sizer.stability(window=60)
    return "持续失败(50% 失败率)", trace, st, {"final_size": trace[-1]["size"]}


def scenario_load_drop():
    change = 60
    sizer, trace = run_scenario(
        "load_drop", lambda r: 0.020 if r < change else 0.001, lambda r: 0.0)
    st = sizer.stability(window=20)
    return "负载骤降(单条耗时 20ms→1ms)", trace, st, {"change_round": change}


def main():
    results = {}
    for fn in (scenario_stable, scenario_slowdown,
               scenario_persistent_failure, scenario_load_drop):
        name, trace, stability, extra = fn()
        print(f"\n=== 场景：{name} ===")
        print_trace(trace)
        print(f"  稳定性(尾部窗口): {json.dumps(stability, ensure_ascii=False)}")
        if extra:
            print(f"  场景指标: {json.dumps(extra, ensure_ascii=False)}")
        results[name] = {"stability": stability, "extra": extra, "trace": trace}

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("\n完整数据已写入 results.json")


if __name__ == "__main__":
    main()
