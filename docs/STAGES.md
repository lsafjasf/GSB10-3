# 阶段划分说明

遗留实现 `legacy_pipeline.py` 把全部逻辑糊在一个串行函数里，没有边界、没有观测、
没有重试。重构后拆为 6 个阶段，中间结果用 dataclass 在 `Context.results` 中
按阶段名显式传递，下一阶段只依赖上一阶段的具名产物。

| 顺序 | 阶段 | 输入（显式中间结果） | 输出 | 副作用 |
|----|----|----|----|----|
| 1 | `parse` | 原始文本行 | `ParseResult(rows, rejects)`：`Row`/`Reject` | 无 |
| 2 | `validate` | `parse` | `ValidateResult(rows, rejects)`：缺字段/非正金额/重复号 | 无 |
| 3 | `enrich` | `validate` | `EnrichResult(orders, rejects)`：汇率换算 + 1% 手续费 | 无 |
| 4 | `aggregate` | `enrich` | `Report(accepted, rejects, total)`，rejects 按原始行号重排 | 无 |
| 5 | `emit_report` | `aggregate` + `sink` | `EmitResult` | 写报表/汇总（幂等键） |
| 6 | `emit_notify` | `aggregate` + `sink` | `EmitResult` | 发通知（幂等键，可跳过） |

只有最后两个阶段产生副作用，其余阶段都是纯函数，重试天然安全。

## 观测钩子

`pipeline.runner.Hooks` 在每个阶段边界提供 4 个回调：
`before_stage`、`after_stage`（含实际尝试次数）、`on_stage_error`（含第几次尝试）、
`on_stage_skipped`。每次运行还在 `ctx.trace` 里留下
`StageTrace(name, status, attempts)` 的结构化轨迹，可接日志/指标/追踪系统。

## 重试与幂等

- 每个 `StageSpec` 带独立的 `RetryPolicy(max_attempts, retry_on)`，默认只对
  `TransientError` 重试，永久性异常（如 `ValueError`）不重试、直接抛出。
- 所有副作用走 `EffectSink.apply(key, payload)`：同一幂等键只真正生效一次；
  阶段在崩溃点之后重试时，已生效的键被跳过，不会重复落盘/外发。
- 幂等断言：
  - `EffectSink.assert_no_duplicate_effects()` 断言无任何副作用键被执行两次；
  - `pipeline/effects.assert_idempotent(sink, stage_fn, ctx)` 把阶段再跑一遍，
    断言副作用快照不变；
  - 同键不同 payload 直接抛 `AssertionError`（幂等键冲突检测）。

## 跳过

`StageSpec.when(ctx)` 返回 `False` 时该阶段整体跳过，轨迹记 `skipped` 并触发
`on_stage_skipped` 钩子，例如 `emit_notify` 在 `options={"notify": False}` 时跳过。
