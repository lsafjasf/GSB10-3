"""漂移复现与修复前后对比。

用可注入的虚拟时钟模拟"长时间运行"（不需要真的等待），
统计实际放行量与配置上限的差额。

运行：python3 reproduce.py
"""

import sys

sys.path.insert(0, "src")

import token_bucket_buggy as buggy_mod
import token_bucket as fixed_mod


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def simulate(bucket_mod, rate, capacity, duration, interval):
    """以 interval 秒的固定节奏连续发请求，持续 duration 秒。"""
    clock = FakeClock()
    bucket = bucket_mod.TokenBucket(rate, capacity, clock=clock)
    allowed = 0
    offered = 0
    t = 0.0
    while t < duration:
        clock.t = t
        if bucket.allow():
            allowed += 1
        offered += 1
        t += interval
    return offered, allowed


def root_cause_evidence():
    """微观证据：同一秒内两次请求，缺陷实现把同一段时间重复补充。"""
    print("== 微观证据（rate=10/s, capacity=10）==")
    for name, mod in (("buggy", buggy_mod), ("fixed", fixed_mod)):
        clock = FakeClock()
        b = mod.TokenBucket(10, 10, clock=clock)
        clock.t = 0.90
        b.allow()
        tokens_after_first = b.tokens
        clock.t = 0.91
        b.allow()
        print(f"  {name:5s}: t=0.90 后 tokens={tokens_after_first:6.3f}  "
              f"t=0.91 后 tokens={b.tokens:6.3f}")
    print("  -> 0.01s 只应补充 0.1 个令牌；buggy 因 int() 截断把")
    print("     0.91s 的 elapsed 重新整段计入，0.90~0.91s 被重复补充。")
    print()


def main():
    scenarios = [
        # rate, capacity, 模拟时长, 请求间隔
        (10, 10, 3600, 0.001),    # 10 req/s, 运行 1 小时
        (100, 100, 86400, 0.001),  # 100 req/s, 运行 24 小时
    ]
    print(f"{'rate':>5} {'dur(s)':>8} {'offered':>12} {'limit':>10} "
          f"{'buggy allowed':>15} {'buggy 漂移':>12} "
          f"{'fixed allowed':>15} {'fixed 漂移':>12}")
    for rate, cap, duration, interval in scenarios:
        offered, allowed_buggy = simulate(
            buggy_mod, rate, cap, duration, interval)
        _, allowed_fixed = simulate(
            fixed_mod, rate, cap, duration, interval)
        # 任意时长内配置允许的上限 = 初始容量 + rate * duration
        limit = cap + rate * duration

        def drift(allowed):
            return (allowed - limit) / limit * 100

        print(f"{rate:5d} {duration:8d} {offered:12d} {int(limit):10d} "
              f"{allowed_buggy:15d} {drift(allowed_buggy):+11.2f}% "
              f"{allowed_fixed:15d} {drift(allowed_fixed):+11.2f}%")
    print()
    root_cause_evidence()


if __name__ == "__main__":
    main()
