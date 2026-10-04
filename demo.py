"""告警抑制与聚合演示脚本：确定性时间线，输出聚合样例 / 抑制记录 / 未受影响清单 / 边界用例。

运行：python3 demo.py
"""

import json

from alert_suppression import (
    Alert,
    AlertPipeline,
    SuppressionRule,
    reconcile,
)


class ManualClock:
    def __init__(self, now=0.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def set(self, now):
        self.now = float(now)


def alert(alert_id, source, alert_type, ts, severity="warning", message=None):
    return Alert(
        alert_id=alert_id,
        source=source,
        alert_type=alert_type,
        severity=severity,
        message=message or "%s %s" % (source, alert_type),
        timestamp=float(ts),
    )


def feed(pipeline, clock, alerts):
    for item in alerts:
        clock.set(item.timestamp)
        pipeline.ingest(item)


def print_header(title):
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def main():
    clock = ManualClock(0.0)
    window = 60.0
    rules = [
        SuppressionRule("R-disk", "db-1", "disk_full", reason="已知磁盘维护，值班已知悉"),
        SuppressionRule("R-deploy", "api-1", "cpu_high", starts_at=100.0, ends_at=200.0,
                        reason="发布窗口 [100,200)"),
        SuppressionRule("R-future", "cache-1", "mem_high", starts_at=10000.0,
                        reason="尚未开始生效的规则"),
    ]
    pipeline = AlertPipeline(rules=rules, window_seconds=window, clock=clock)

    alerts = []
    # 同源风暴：host-7 cpu_high，0~57s 内 20 条（无规则覆盖，应被聚合成 1 条）
    alerts += [alert("storm%02d" % i, "host-7", "cpu_high", i * 3,
                     message="cpu 95%") for i in range(20)]
    alerts.append(alert("storm20", "host-7", "cpu_high", 65, message="cpu 96%"))
    # 被永久规则覆盖的 3 条磁盘关键告警（db-1）
    alerts += [
        alert("d1", "db-1", "disk_full", 0, severity="critical", message="/data 使用率 98%"),
        alert("d2", "db-1", "disk_full", 5, severity="warning", message="/data 使用率 92%"),
        alert("d3", "db-1", "disk_full", 12, severity="critical", message="/data 使用率 99%"),
    ]
    # 同类型但不同来源：不能被 R-disk 误伤
    alerts.append(alert("d4", "db-2", "disk_full", 3, message="/data 使用率 91%"))
    # 发布窗口内 3 条关键告警被抑制，窗口外 1 条保留
    alerts += [
        alert("p1", "api-1", "cpu_high", 150, severity="critical", message="发布期抖动"),
        alert("p2", "api-1", "cpu_high", 160, severity="critical", message="发布期抖动"),
        alert("p3", "api-1", "cpu_high", 170, severity="critical", message="发布期抖动"),
        alert("p4", "api-1", "cpu_high", 200, severity="critical", message="窗口结束后仍高"),
    ]
    # 规则尚未生效：cache-1 不受影响
    alerts.append(alert("m1", "cache-1", "mem_high", 300, message="mem 88%"))
    # 半开窗口边界：[600,660) 两条 + t=660 新窗口一条
    alerts += [
        alert("b1", "host-9", "net_loss", 600, message="丢包 5%"),
        alert("b2", "host-9", "net_loss", 659.99, message="丢包 7%"),
        alert("b3", "host-9", "net_loss", 660, message="丢包 9%"),
    ]

    alerts.sort(key=lambda a: (a.timestamp, a.alert_id))
    feed(pipeline, clock, alerts)
    pipeline.flush()

    print_header("0. 输入与规则（窗口 %ss，半开区间 [start, start+%ss)）" % (window, window))
    print("输入告警 %d 条；规则 %d 条：" % (len(alerts), len(rules)))
    for rule in rules:
        print("  - %s: 精确覆盖 (%s, %s)  生效区间 [%s, %s)  原因: %s"
              % (rule.rule_id, rule.source, rule.alert_type,
                 rule.starts_at if rule.starts_at is not None else "-inf",
                 rule.ends_at if rule.ends_at is not None else "+inf", rule.reason))

    # 1. 聚合样例
    print_header("1. 聚合样例（同来源+同类型，窗口内合并并保留计数）")
    sample = [a.to_dict() for a in pipeline.aggregated]
    print(json.dumps(sample, ensure_ascii=False, indent=2, sort_keys=True))

    # 2. 抑制记录
    print_header("2. 抑制记录（被压制告警逐条可追溯）")
    print("共 %d 条：" % len(pipeline.suppressed))
    for record in pipeline.suppressed:
        a = record.alert
        print("  - 规则 %-8s 告警 %-7s t=%-6g %-7s/%-9s %-8s %s"
              % (record.rule_id, a.alert_id, a.timestamp, a.source, a.alert_type,
                 a.severity, a.message))

    # 3. 未受影响告警完整清单
    print_header("3. 未受影响告警完整清单（未匹配任何规则，%d 条，不重不漏）"
                 % len(pipeline.unaffected))
    for occ in pipeline.unaffected:
        a = occ.alert
        print("  - %-7s t=%-6g %-7s/%-9s %-8s %s"
              % (a.alert_id, a.timestamp, a.source, a.alert_type, a.severity, a.message))

    # 4. 边界用例
    print_header("4. 边界用例")
    boundary_cases()

    # 5. 对账
    print_header("5. 对账（抑制记录 ∪ 聚合成员 == 输入，且仅出现一次）")
    result = reconcile([a.alert_id for a in alerts], pipeline)
    print("输入 %d = 抑制 %d + 聚合成员 %d"
          % (result.total_input, result.suppressed, result.aggregated_members))
    print("缺失 id: %s；重复 id: %s" % (list(result.missing_ids), list(result.duplicate_ids)))
    print("对账结果:", "OK" if result.ok else "FAILED")


def boundary_cases():
    # 单条告警：窗口内只有 1 条，聚合后 count=1
    c = ManualClock(0.0)
    p = AlertPipeline(window_seconds=60.0, clock=c)
    feed(p, c, [alert("single", "host-x", "temp_high", 10, severity="warning")])
    p.flush()
    print("[单条告警]       聚合条数=%d, count=%d, alert_ids=%s"
          % (len(p.aggregated), p.aggregated[0].count, p.aggregated[0].alert_ids))

    # 同源风暴：1000 条全部被精确规则抑制，逐条留痕；同机其他类型不受影响
    c = ManualClock(0.0)
    p = AlertPipeline(rules=[SuppressionRule("R-storm", "host-7", "cpu_high")],
                      window_seconds=60.0, clock=c)
    storm = [alert("s%04d" % i, "host-7", "cpu_high", i * 0.05) for i in range(1000)]
    storm.append(alert("keep", "host-7", "disk_full", 1, severity="critical"))
    feed(p, c, storm)
    p.flush()
    print("[同源风暴]       抑制 %d 条（逐条记录），聚合输出 %d 条；"
          "同机 disk_full 保留: %s"
          % (len(p.suppressed), len(p.aggregated),
             [occ.alert.alert_id for occ in p.unaffected]))

    # 抑制覆盖关键告警：窗内 critical 被压，记录中保留原始严重级别
    c = ManualClock(0.0)
    p = AlertPipeline(rules=[SuppressionRule("R", "api-1", "cpu_high", 100.0, 200.0)],
                      window_seconds=60.0, clock=c)
    feed(p, c, [alert("crit-in", "api-1", "cpu_high", 150, severity="critical")])
    rec = p.suppressed[0]
    print("[覆盖关键告警]   crit-in(severity=%s) 被规则 %s 抑制，可追溯: %s"
          % (rec.alert.severity, rec.rule_id, rec.alert.alert_id))

    # 规则过期：t == ends_at 时规则已失效（半开区间），告警保留
    c = ManualClock(0.0)
    p = AlertPipeline(rules=[SuppressionRule("R", "api-1", "cpu_high", 0.0, 200.0)],
                      window_seconds=60.0, clock=c)
    feed(p, c, [alert("crit-out", "api-1", "cpu_high", 200, severity="critical")])
    p.flush()
    print("[规则过期]       t=200(=ends_at) 抑制记录 %d 条，未受影响清单: %s"
          % (len(p.suppressed), [occ.alert.alert_id for occ in p.unaffected]))

    # 聚合窗口边界：t=60 恰好是边界点，归入下一窗口
    c = ManualClock(0.0)
    p = AlertPipeline(window_seconds=60.0, clock=c)
    feed(p, c, [
        alert("w1a", "host-9", "net_loss", 0),
        alert("w1b", "host-9", "net_loss", 59.99),
        alert("w2a", "host-9", "net_loss", 60),
    ])
    p.flush()
    print("[窗口半开边界]   两窗口计数: %s"
          % ([(a.first_seen, a.count) for a in p.aggregated],))


if __name__ == "__main__":
    main()
