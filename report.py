"""采样率对照数据 + 请求级一致性断言演示。

运行：python3 report.py
"""

from sampler import Sampler, SamplingRule

N_REQUESTS = 20000   # 每个场景模拟的请求数
LOGS_PER_REQUEST = 5  # 每个请求内同类日志条数（用于一致性验证）


def simulate(sampler: Sampler, level: str, channel: str, n: int) -> None:
    for i in range(n):
        sampler.should_keep(level, channel, f"req-{i}")


def print_report(title: str, sampler: Sampler) -> None:
    print(f"\n== {title} ==")
    print(f"{'规则键':<16}{'目标率':>8}{'实际率':>10}{'偏差':>10}{'样本数':>10}{'保留数':>10}")
    for row in sampler.report():
        print(
            f"{row['rule_key']:<16}{row['target_rate']:>8.2f}"
            f"{row['actual_rate']:>10.4f}{row['deviation']:>+10.4f}"
            f"{row['total']:>10}{row['kept']:>10}"
        )


def assert_consistency(sampler: Sampler, level: str, channel: str, n: int) -> None:
    """请求级一致性断言：同一请求内命中同一规则的所有日志全留或全丢。"""
    violations = 0
    for i in range(n):
        rid = f"consistency-{i}"
        decisions = {sampler.should_keep(level, channel, rid) for _ in range(LOGS_PER_REQUEST)}
        if len(decisions) != 1:
            violations += 1
    status = "通过" if violations == 0 else f"失败({violations} 处)"
    print(f"一致性断言 [{level}/{channel}] {n} 个请求 x {LOGS_PER_REQUEST} 条日志: {status}")
    assert violations == 0


def main() -> None:
    # 场景 1：混合级别 + 关键通道保护（错误不采、支付链路不丢、调试全丢）
    s1 = Sampler(
        rules=[
            SamplingRule(rate=1.0, level="ERROR"),
            SamplingRule(rate=1.0, channel="payment"),
            SamplingRule(rate=0.5, level="INFO"),
            SamplingRule(rate=0.1, level="WARN"),
            SamplingRule(rate=0.0, level="DEBUG"),
        ]
    )
    print("规则匹配优先级：")
    for line in s1.describe_priority():
        print("  " + line)
    simulate(s1, "ERROR", "app", N_REQUESTS)
    simulate(s1, "INFO", "payment", N_REQUESTS)
    simulate(s1, "INFO", "search", N_REQUESTS)
    simulate(s1, "WARN", "app", N_REQUESTS)
    simulate(s1, "DEBUG", "app", N_REQUESTS)
    print_report("场景1：混合级别 + 关键通道保护", s1)
    assert_consistency(s1, "INFO", "search", 5000)
    assert_consistency(s1, "WARN", "app", 5000)

    # 场景 2：全保留 / 全丢弃边界
    s2 = Sampler(rules=[SamplingRule(rate=1.0, level="ERROR"),
                        SamplingRule(rate=0.0, level="DEBUG")])
    simulate(s2, "ERROR", "app", N_REQUESTS)
    simulate(s2, "DEBUG", "app", N_REQUESTS)
    print_report("场景2：全保留 / 全丢弃", s2)

    # 场景 3：规则热更新前后对照
    s3 = Sampler(rules=[SamplingRule(rate=0.1, level="INFO")])
    simulate(s3, "INFO", "app", N_REQUESTS)
    print_report("场景3a：更新前 INFO=0.1", s3)
    s3.update_rules([SamplingRule(rate=0.9, level="INFO")])
    simulate(s3, "INFO", "app", N_REQUESTS)
    print_report("场景3b：热更新后 INFO=0.9（统计已重置）", s3)

    print("\n全部一致性断言通过，最大偏差:",
          f"{max(s1.max_abs_deviation(), s3.max_abs_deviation()):.4f}")


if __name__ == "__main__":
    main()
