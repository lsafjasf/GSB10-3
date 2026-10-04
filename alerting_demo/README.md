# 告警抑制与聚合（Python 3 标准库）

纯标准库实现，时间可注入（`clock` 无参可调用对象，默认 `time.time`）。

## 文件

| 文件 | 说明 |
| --- | --- |
| `alerting.py` | 库：`Alert` / `SuppressionRule` / `SuppressionRecord` / `AggregatedAlert` / `AlertAggregator` / `AlertEngine` |
| `test_alerting.py` | 自测，14 个用例，覆盖全部要求情形与边界 |
| `demo.py` | 端到端演示，输出聚合样例、抑制记录、未受影响清单 |

## 运行方式

```bash
cd alerting_demo
python3 test_alerting.py   # 自测（14 tests）
python3 demo.py            # 场景演示，输出可复现
```

Python >= 3.8（使用了 `dataclasses`、`enum.IntEnum` 标准特性）。

## 设计要点

- **聚合**：按 `(source, alert_type)` 分组合并；滑动窗口（默认 60s），
  窗口边界左闭右闭——距 `first_seen` 恰好等于窗口长度仍属同组，超过才冲刷；
  聚合结果保留 `count`、`first_seen`、`last_seen`、`max_severity`、全部成员 ID。
- **抑制规则**：所有已设置字段 AND 匹配（`source` / `alert_type` 支持
  `fnmatch` 通配符、`min_severity` 限级别、`labels` 精确键值），
  可设 `starts_at` / `expires_at` 生效窗（左闭右开）。
  构造时至少要求一个匹配字段，禁止"一条规则压全部"。
- **可追溯**：被压制告警不进入聚合器，但每条生成 `SuppressionRecord`
  （告警原文 + 命中规则 + 原因 + 时间）。
- **互不影响**：未匹配规则的告警进入聚合器，同时原样保存在
  `engine.unaffected` 完整清单中。
- **时间注入**：引擎构造参数 `clock=callable`，`ingest(alert, timestamp=...)`
  也可显式给时间；测试与演示均为确定性输出。

## 情形与用例对照

| 要求情形 | 测试用例 |
| --- | --- |
| 单条告警被覆盖 | `test_single_alert_suppressed` |
| 同源风暴聚合 + 计数 | `test_storm_merged_with_count` |
| 窗口滚动 / 边界恰好相等 | `test_window_rollover_creates_new_group`、`test_window_boundary_inclusive` |
| 不同来源不合并 | `test_different_sources_not_merged` |
| 抑制不覆盖关键告警 | `test_critical_alert_passes_through`、`test_label_scoped_rule_does_not_leak` |
| 规则过期 / 未生效 | `test_expired_rule_stops_suppressing`、`test_rule_not_yet_active` |
| 未匹配告警完整清单 | `test_unmatched_alerts_untouched_and_listed` |
| 抑制记录可追溯 | `test_suppression_record_is_traceable` |
| 时间注入 | `test_injected_clock_controls_suppression_window`、`test_explicit_timestamp_overrides_clock` |
| 空规则禁止 | `test_rule_requires_at_least_one_criterion` |
