# 边界用例

`data/orders.csv` 与 `tests/test_pipeline.py::TestDifferential.test_matches_legacy_on_edge_cases`
覆盖以下边界，全部要求重构前后输出逐字节一致：

| 用例 | 期望行为 |
|----|----|
| 空输入 / 只有空行 | 无 ORDER、无 NOTIFY，SUMMARY 全零 |
| 负金额 `-5` | `REJECT ... amount_not_positive` |
| 零金额 `0` | `REJECT ... amount_not_positive` |
| 缺客户名 | `REJECT ... missing_customer` |
| 金额不可解析（`abc`/`x`） | `REJECT ... bad_amount`（parse 阶段拒绝） |
| 列数不为 4 | `REJECT ... bad_row` |
| 全空字段 `,,,` | `REJECT ? bad_row` |
| 未知币种 `JPY`/`GBP` | `REJECT ... unknown_currency`（enrich 阶段拒绝） |
| 小写币种 `usd` | 规范化为 USD 正常受理 |
| 重复订单号 `A001` | 第二次出现 `REJECT A001 duplicate_id` |
| 小数位 4 位 `10.005` | ROUND_HALF_UP 精确舍入：71.04 + 手续费 0.71 = 71.75 |
| 副作用阶段中途崩溃 | 重试后成功，副作用不重复，最终输出一致 |
| 重试次数耗尽 | 抛 `TransientError`，trace 记 `failed`/`attempts=3` |
| 非瞬时错误 | 不重试（只尝试 1 次） |
| `emit_notify` 被跳过 | outbox 为空，report 不变 |

关键金额手算对拍（汇率 USD=7.10、EUR=7.80、CNY=1.00，手续费 1%）：
A001 100.00 USD -> 710.00 + 7.10 = 717.10；
A003 250.5 USD -> 1778.55 + 17.79 = 1796.34；
A005 42 EUR -> 327.60 + 3.28 = 330.88；
A008 10.005 USD -> 71.04 + 0.71 = 71.75；
合计 2916.07 CNY。
