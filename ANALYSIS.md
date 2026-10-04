# 订单履约状态机行为摸底分析

- 被测代码：`legacy/legacy_order_machine.py`（276 行，分析期间未做任何修改，
  sha256 = `fb84e95f64fd291924515084f5d0ec41cc6ade9b7cf966b30423f03218463560`）
- 业务文档：`docs/state_machine_spec.md`（最后更新 2019-05，已过时）
- 分析工具：`analysis/`（纯 Python 3 标准库，只读导入被测代码）
- 一键复现：仓库根目录执行 `python3 analysis/run_all.py`

## 1. 分析方法

| 手段 | 脚本 | 说明 |
| --- | --- | --- |
| 静态扫描 | `analysis/static_scan.py` | 用 `ast` 解析 `fire()`，提取每个状态区块 / 事件分支的行号、目标状态、守卫表达式；对常量守卫定值求值，标记死分支；沿活分支做静态 BFS 求可达状态集 |
| 动态探测 | `analysis/probe_dynamic.py` | 从 `CREATED` 做 BFS，每个可达状态 × 每个已注册事件各打一遍，按返回值 / 异常 / `history` / `ignored` 账本分类结果 |
| 文档对拍 | `analysis/doc_diff.py` | 解析文档转移表逐条驱动真实机器对拍；另含两个专项实验 |
| 非法事件矩阵 | `analysis/illegal_events.py` | 分三层（未注册事件 / 已注册但不适用 / 不可达状态内部）逐项定性抛错还是静默忽略 |

结构化结果写入 `analysis/out/*.json`，可重复生成。

## 2. 状态清单

实现里出现过的状态共 12 个（`ALL_STATES`，`legacy/legacy_order_machine.py:47`）：

| 状态 | 运行时可达 | 说明 | 证据 |
| --- | --- | --- | --- |
| `CREATED` | 是（初始态） | 已下单未支付 | `legacy_order_machine.py:151` |
| `PAID` | 是 | 已支付待仓库 | `legacy_order_machine.py:169` |
| `PICKING` | 是 | 拣货中 | `legacy_order_machine.py:186` |
| `PACKED` | 是 | 已打包待发货 | `legacy_order_machine.py:200` |
| `SHIPPED` | 是 | 在途 | `legacy_order_machine.py:210` |
| `DELIVERED` | 是 | 已签收 | `legacy_order_machine.py:231` |
| `REFUNDING` | 是 | 退款中（自动审批，一步完成） | `legacy_order_machine.py:249` |
| `CLOSED` | 是（终态） | 关闭 | 终态拦截 `legacy_order_machine.py:143` |
| `CANCELLED` | 是（终态） | 取消 | 同上 |
| `REFUNDED` | 是（终态） | 退款完成 | 同上 |
| `AUDITING` | **否（死状态）** | 风控人工审核；全代码无任何 `_go('AUDITING')`，区块整体不可达 | `legacy_order_machine.py:265` |
| `ARCHIVED` | **否（死状态）** | 归档；唯一来源是 `migrate` 死分支（见 §4） | `legacy_order_machine.py:240` |

可达性同时由静态 BFS（`analysis/out/static.json` 的 `static_reachable_states`）
与动态 BFS（`analysis/out/dynamic.json` 的 `reachable_states`）独立确认，
两者一致：10 个可达，2 个不可达。

## 3. 事件清单

已注册事件 14 个（`KNOWN_EVENTS`，`legacy/legacy_order_machine.py:26`）：

`pay` `cancel` `timeout` `stock_ok` `stock_out` `pick_done` `ship`
`deliver` `close` `return_request` `refund_approve` `refund_done`
`audit_pass` `audit_fail`

另有 3 个"幽灵事件"：

| 事件 | 出处 | 实际行为 |
| --- | --- | --- |
| `flag_risk` | 仅存在于文档（spec §2） | 未注册，触发即 `UnknownEventError` |
| `migrate` | 代码里有分支（`legacy_order_machine.py:240`）但未注册进 `KNOWN_EVENTS` | 永远被第 0 层校验拦截，分支为死代码 |
| `retry` 等任何拼错名 | — | 同上，`UnknownEventError` |

## 4. 完整状态转移表（实测）

符号：`→X` 转移到 X；`↺stay` 自循环（记入 `history`）；`∅ignore` 静默忽略
（只记 `ignored` 账本）；`✗raise` 抛 `InvalidTransitionError`。
完整 10×14 矩阵见 `analysis/out/dynamic.json`，下表只列非 `✗raise` 项。

| 当前状态 | 事件 | 实测结果 | 代码位置 |
| --- | --- | --- | --- |
| CREATED | pay | →PAID（`amount<=0` 时改为 ✗raise） | `legacy_order_machine.py:152` |
| CREATED | cancel | →CANCELLED | `legacy_order_machine.py:158` |
| CREATED | timeout | →CANCELLED | `legacy_order_machine.py:161` |
| PAID | stock_ok | →PICKING | `legacy_order_machine.py:170` |
| PAID | stock_out | →REFUNDING | `legacy_order_machine.py:172` |
| PAID | cancel | →REFUNDING | `legacy_order_machine.py:175` |
| PAID | timeout | ↺stay | `legacy_order_machine.py:178` |
| PICKING | pick_done | →PACKED | `legacy_order_machine.py:187` |
| PICKING | stock_out | →REFUNDING | `legacy_order_machine.py:189` |
| PICKING | cancel | →REFUNDING | `legacy_order_machine.py:192` |
| PACKED | ship | →SHIPPED | `legacy_order_machine.py:201` |
| PACKED | cancel | →REFUNDING | `legacy_order_machine.py:203` |
| SHIPPED | deliver | →DELIVERED | `legacy_order_machine.py:211` |
| SHIPPED | timeout | ↺stay；累计满 3 次才 →DELIVERED | `legacy_order_machine.py:213`（计数 `:216`、阈值 `:217`、常量 `:63`） |
| SHIPPED | cancel | ∅ignore | `legacy_order_machine.py:220` |
| SHIPPED | pay | ∅ignore | `legacy_order_machine.py:223` |
| DELIVERED | close | →CLOSED | `legacy_order_machine.py:232` |
| DELIVERED | deliver | ∅ignore | `legacy_order_machine.py:234` |
| REFUNDING | refund_done | →REFUNDED | `legacy_order_machine.py:250` |
| CLOSED / CANCELLED / REFUNDED | 任意已注册事件 | ∅ignore（终态统一拦截） | `legacy_order_machine.py:143` |
| AUDITING（不可达） | audit_pass | →CLOSED（仅白盒探针可验证） | `legacy_order_machine.py:266` |
| AUDITING（不可达） | audit_fail | →CANCELLED（仅白盒探针可验证） | `legacy_order_machine.py:268` |

## 5. 死代码清单

| # | 位置 | 内容 | 死因 | 验证方式 |
| --- | --- | --- | --- | --- |
| D1 | `legacy_order_machine.py:265-270` | 整个 `AUDITING` 状态区块（3 个分支） | 全代码无任何 `_go('AUDITING')`；文档所述入口事件 `flag_risk` 未注册 | 静态 BFS + 动态 BFS 均不可达；白盒置 `state='AUDITING'` 后分支本身可执行 |
| D2 | `legacy_order_machine.py:240` | `DELIVERED + migrate → ARCHIVED` | `migrate` 不在 `KNOWN_EVENTS`，第 0 层校验（`:137`）先拦截 | 静态扫描标记 `event-not-registered`；实测 `fire('migrate')` 抛 `UnknownEventError` |
| D3 | `legacy_order_machine.py:237` | `DELIVERED + return_request → REFUNDING` | 守卫 `self._legacy_v1_returns` 在 `__init__`（`:88`）恒为 `False` | 静态定值求值标记 `guard-statically-false`；实测 `fire('return_request')` 落入 `:244` 抛错 |
| D4 | `legacy_order_machine.py:273` | 防御性兜底 `raise`（未知状态） | 正常路径下 `state` 只会取上述 12 个值之一 | 仅白盒改写 `state` 为未知值可触发 |

连带结论：`ARCHIVED` 状态（`ALL_STATES` 中声明）因唯一入口 D2 死亡而不可达。

## 6. 实现与文档的差异（全部经实验复现）

对拍实验：`python3 analysis/doc_diff.py`，结果存 `analysis/out/doc_diff.json`。
文档 20 行转移中 **8 行一致、9 行不一致、3 行无法对拍**（源状态不可达）。

| # | 文档声称（spec 行号） | 实测行为 | 证据位置 |
| --- | --- | --- | --- |
| M1 | `CREATED + flag_risk → AUDITING`（spec:30） | 抛 `UnknownEventError`；`flag_risk` 未注册，`AUDITING` 不可达 | `legacy_order_machine.py:26`（事件清单）、`:137`（闸门） |
| M2 | `PAID + stock_out → PAID`（等待补货，spec:32） | →REFUNDING | `legacy_order_machine.py:172` |
| M3 | `PAID + cancel → CANCELLED`（spec:33） | →REFUNDING（已付款必须先退款） | `legacy_order_machine.py:175` |
| M4 | `PICKING + cancel → CANCELLED`（spec:35） | →REFUNDING | `legacy_order_machine.py:192` |
| M5 | `PACKED + cancel → CANCELLED`（spec:37） | →REFUNDING | `legacy_order_machine.py:203` |
| M6 | `SHIPPED + timeout → DELIVERED`（立即签收，spec:39） | 前 2 次 ↺stay，第 3 次才 →DELIVERED | `legacy_order_machine.py:213-218`；专项实验 1 输出 |
| M7 | `SHIPPED + cancel →（报错）`（spec:40） | 静默忽略 | `legacy_order_machine.py:220` |
| M8 | `DELIVERED + return_request → REFUNDING`（spec:42） | 抛 `InvalidTransitionError`（分支被恒假守卫封死，即 D3） | `legacy_order_machine.py:237`、`:88` |
| M9 | `REFUNDING + refund_approve → REFUND_APPROVED`（spec:43） | 抛 `InvalidTransitionError`；退款一步完成，无审批态 | `legacy_order_machine.py:252` |
| M10 | 状态 `REFUND_APPROVED` 存在（spec:20、44） | 实现中不存在该状态 | `legacy_order_machine.py:47` |
| M11 | "任何状态下非法事件一律抛 `InvalidTransitionError`"（spec §3） | 终态静默忽略一切已注册事件；`SHIPPED` 忽略 `cancel`/`pay`；`DELIVERED` 忽略 `deliver` | `legacy_order_machine.py:143`、`:220`、`:223`、`:234` |
| M12 | 文档未记载 | `PAID + timeout` ↺stay；`CREATED + pay(amount<=0)` 拒绝；终态忽略语义 | `legacy_order_machine.py:178`、`:155`、`:143` |

无法对拍的 3 行（spec:45-46 的 AUDITING 两行与 spec:44 的
REFUND_APPROVED 行）源状态运行时不可达，本身即差异证据。

## 7. 非法事件：抛错还是静默忽略（逐项结论）

实验：`python3 analysis/illegal_events.py`，结果存 `analysis/out/illegal_events.json`。

**层次 A —— 未注册事件**（不在 `KNOWN_EVENTS`）：
任何状态（含终态、含白盒进入的 AUDITING）一律抛 `UnknownEventError`。
原因：第 0 层校验（`legacy_order_machine.py:137`）先于终态拦截（`:143`）。

**层次 B —— 已注册但该状态不适用的事件**：

| 状态 | 抛 `InvalidTransitionError` | 静默忽略 | 自循环 stay |
| --- | --- | --- | --- |
| CREATED | 其余 11 个事件 | 无 | 无 |
| PAID | 其余 10 个事件 | 无 | timeout |
| PICKING | 其余 11 个事件 | 无 | 无 |
| PACKED | 其余 12 个事件 | 无 | 无 |
| SHIPPED | 其余 10 个事件 | cancel、pay | timeout |
| DELIVERED | 其余 12 个事件 | deliver | 无 |
| REFUNDING | 其余 13 个事件 | 无 | 无 |
| CLOSED（终态） | 无 | 全部 14 个 | 无 |
| CANCELLED（终态） | 无 | 全部 14 个 | 无 |
| REFUNDED（终态） | 无 | 全部 14 个 | 无 |

**层次 C —— 不可达状态 AUDITING 内部**（白盒探针）：
`audit_pass`/`audit_fail` 之外的已注册事件抛 `InvalidTransitionError`，
未注册事件抛 `UnknownEventError`；分支逻辑正常，只是永远进不来。

**关键结论**："抛错还是静默忽略"在实现里并不统一——非终态默认抛错，
但存在 4 个显式静默忽略点（`:220`、`:223`、`:234` 及终态拦截 `:143`），
与文档 §3 的"一律抛错"声明直接冲突（差异 M11）。

## 8. 运行方式

```bash
cd <仓库根目录>
python3 analysis/run_all.py        # 一键跑全部 4 个实验
# 或单独运行：
python3 analysis/static_scan.py    # 静态扫描 → analysis/out/static.json
python3 analysis/probe_dynamic.py  # 动态矩阵 → analysis/out/dynamic.json
python3 analysis/doc_diff.py       # 文档对拍 → analysis/out/doc_diff.json
python3 analysis/illegal_events.py # 非法事件矩阵 → analysis/out/illegal_events.json
```

环境要求：Python 3.9+（用到 `ast.unparse`），仅标准库，无需安装任何依赖。
被测代码在分析全程未被修改；所有实验只通过 `fire()` 公开接口驱动，
唯一的白盒手段是在实验机实例上直接赋值 `.state` 以验证不可达状态
（已在 §5 D1 与 §7 层次 C 中明确标注）。

## 9. 产物清单

| 文件 | 作用 |
| --- | --- |
| `legacy/legacy_order_machine.py` | 被测状态机（未修改） |
| `docs/state_machine_spec.md` | 过时的业务文档（对拍基准） |
| `analysis/common.py` | 实验共用驱动（BFS、结果分类、白盒探针） |
| `analysis/static_scan.py` | AST 静态扫描与死代码判定 |
| `analysis/probe_dynamic.py` | 动态转移矩阵与可达性 |
| `analysis/doc_diff.py` | 文档差异对拍 + 两个专项实验 |
| `analysis/illegal_events.py` | 非法事件三层矩阵 |
| `analysis/run_all.py` | 一键运行入口 |
| `analysis/out/*.json` | 各实验的结构化结果（可重复生成） |
| `ANALYSIS.md` | 本文档 |
