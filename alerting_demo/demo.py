"""端到端演示：同源风暴、单条抑制、关键告警放行、规则过期。

运行：python3 demo.py
时间使用内置假时钟，输出可复现。
"""

from alerting import Alert, AlertEngine, Severity, SuppressionRule


class FakeClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def alert(alert_id, source, alert_type, severity, message, ts,
          labels=None):
    return Alert.create(
        alert_id=alert_id,
        source=source,
        alert_type=alert_type,
        severity=severity,
        message=message,
        timestamp=ts,
        labels=labels,
    )


def main():
    clock = FakeClock(1000.0)

    rules = [
        # R1：精确压制 host-1 维护期间的 CPU 告警（1000~1100 生效）
        SuppressionRule.create(
            rule_id="R1-host1-cpu-maintenance",
            reason="host-1 计划内内核升级，1000~1100 静默 CPU 告警",
            source="host-1",
            alert_type="cpu_high",
            starts_at=1000.0,
            expires_at=1100.0,
        ),
        # R2：只压 web-* 的 WARNING 及以下 5xx 噪音；CRITICAL 永远放行
        SuppressionRule.create(
            rule_id="R2-suppress-warning-noise",
            reason="web-* 的 WARNING 级别 5xx 噪音",
            source="web-*",
            alert_type="http_5xx",
            min_severity=Severity.WARNING,
        ),
    ]

    engine = AlertEngine(rules=rules, window_seconds=60.0, clock=clock)

    # 1) 单条/批量抑制：host-1 在维护窗口里刷 20 条 cpu_high —— 全部被 R1 压制
    for i in range(20):
        clock.t = 1000.0 + i
        engine.ingest(
            alert(
                f"cpu-{i:02d}", "host-1", "cpu_high", Severity.WARNING,
                f"cpu usage {90 + i}%", 1000.0 + i,
            )
        )

    # 2) 同源风暴（不匹配任何规则）：api-1 在 21 秒内报 8 条 5xx —— 聚合成 1 条
    for i in range(8):
        clock.t = 1010.0 + i * 3
        engine.ingest(
            alert(
                f"api-{i:02d}", "api-1", "http_5xx", Severity.WARNING,
                f"5xx rate {5 + i}%", 1010.0 + i * 3,
            )
        )

    # 3) 抑制不得覆盖关键告警：web-2 的 5xx 噪音被 R2 压制，
    #    但同来源同类型的 CRITICAL 必须放行
    clock.t = 1030.0
    engine.ingest(
        alert("web-noise-01", "web-2", "http_5xx", Severity.WARNING,
              "5xx rate 6%", 1030.0)
    )
    clock.t = 1035.0
    engine.ingest(
        alert(
            "web-crit-01", "web-2", "http_5xx", Severity.CRITICAL,
            "5xx rate 80%，整站不可用", 1035.0,
        )
    )

    # 4) 未匹配任何规则的独立告警（不同来源/不同类型）
    clock.t = 1040.0
    engine.ingest(
        alert("db-01", "db-1", "replication_lag", Severity.ERROR,
              "从库延迟 120s", 1040.0)
    )
    clock.t = 1042.0
    engine.ingest(
        alert("host1-disk-01", "host-1", "disk_full", Severity.ERROR,
              "磁盘使用 97%", 1042.0)
    )

    # 5) 规则过期：维护窗口结束（>=1100）后再来的 cpu_high 不再被压制
    clock.t = 1150.0  # 维护窗口已结束
    engine.ingest(
        alert("cpu-late", "host-1", "cpu_high", Severity.WARNING,
              "cpu usage 99%（维护已结束）", 1150.0)
    )

    groups = engine.close_window()

    print("=" * 72)
    print("一、聚合样例（窗口 60s，按 来源+类型 合并，保留计数）")
    print("=" * 72)
    for g in sorted(groups, key=lambda x: x.key):
        print(
            f"  source={g.source:<8} type={g.alert_type:<16} "
            f"count={g.count:<3} max_severity={g.max_severity.name:<8} "
            f"window=[{g.first_seen:.0f}, {g.last_seen:.0f}]"
        )
        print(f"    members={g.member_ids}")

    print()
    print("=" * 72)
    print("二、抑制记录（每条被压制告警均可追溯）")
    print("=" * 72)
    print(f"  共 {len(engine.suppression_records)} 条被压制告警")
    for r in engine.suppression_records:
        d = r.to_dict()
        print(
            f"  alert={d['alert_id']:<14} source={d['source']:<7} "
            f"rule={d['rule_id']:<30} at={d['suppressed_at']:.0f}"
        )
        print(f"    reason: {d['reason']}")

    print()
    print("=" * 72)
    print("三、未受抑制影响的告警完整清单")
    print("=" * 72)
    print(f"  共 {len(engine.unaffected)} 条，全部进入聚合通道，无一丢失：")
    for a in engine.unaffected:
        print(
            f"  {a.alert_id:<14} source={a.source:<7} "
            f"type={a.alert_type:<16} severity={a.severity.name:<8} "
            f'"{a.message}"'
        )

    print()
    print("=" * 72)
    print("四、关键边界结论")
    print("=" * 72)
    print("  - 维护窗口内 20 条 cpu_high（cpu-00..cpu-19）全部被 R1 压制，"
          "且每条都有抑制记录")
    print("  - api-1 的 8 条 5xx 未匹配任何规则，聚合成 1 条（count=8）")
    print("  - web-noise-01 被 R2 压制，但 CRITICAL 的 web-crit-01 放行")
    print("  - 规则过期后 cpu-late 正常进入聚合，R1 不再产生抑制记录")
    print("  - db-01 / host1-disk-01 无任何规则匹配，原样保留")


if __name__ == "__main__":
    main()
