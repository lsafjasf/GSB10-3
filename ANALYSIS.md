# 订单履约状态机分析报告

- 被测代码：`legacy/legacy_order_machine.py`（276 行，单文件 if/elif 状态机）
- 对照文档：`docs/state_machine_spec.md`（最后更新 2019-05-12，已过时）
- 分析方式：静态通读 + 动态探针实测（只读调用，未修改被测代码，
  sha256 `fb84e95f…3560`，每次 `run_all.py` 运行前后自动校验指纹）
- 运行环境：Python 3.12，仅标准库

## 运行方式

```bash
cd <本目录>
python3 experiments/run_all.py        # 一键跑全部 3 组实验 + 完整性校验
python3 experiments/01_static_scan.py     # 实验1：清单与可达性 BFS
python3 experiments/02_doc_diff.py        # 实验2：文档差异场景复现
python3 experiments/03_illegal_events.py  # 实验3：非法事件穷举矩阵
```

机器可读结果在 `experiments/results/01_static.json`、`02_doc_diff.json`、
`03_illegal.json`。被测代码来自分支 `origin/230-A` 的 `legacy/` 与 `docs/`，
原样复制，未做任何改动。

---

## 一、状态与事件清单（实测，证据：`experiments/results/01_static.json`）

### 状态（代码声明 12 个，`ALL_STATES`，`legacy_order_machine.py:47-60`）

| 状态 | 运行时可达 | 说明 |
| --- | --- | --- |
| `CREATED` | ✅ 初始状态 | `__init__`，`legacy_order_machine.py:79` |
| `PAID` | ✅ | |
| `PICKING` | ✅ | |
| `PACKED` | ✅ | |
| `SHIPPED` | ✅ | |
| `DELIVERED` | ✅ | |
| `REFUNDING` | ✅ | 文档未列出从 PAID/PICKING/PACKED 直达此处的路径 |
| `CLOSED` | ✅ 终态 | `TERMINAL_STATES`，`legacy_order_machine.py:44` |
| `CANCELLED` | ✅ 终态 | 同上 |
| `REFUNDED` | ✅ 终态 | 同上 |
| `AUDITING` | ❌ 不可达 | 没有任何已知事件能进入（`flag_risk` 已从事件清单移除），分支成死代码 |
| `ARCHIVED` | ❌ 不可达 | 唯一入口依赖未注册事件 `migrate`，且无任何状态分支处理它 |

可达性由 BFS 实测确认（实验 1）：从 `CREATED` 只发已知事件，能到达的
状态恰为上表 10 个 ✅；`AUDITING`/`ARCHIVED` 不可达。
文档中的 `REFUND_APPROVED` 在代码里**根本不存在**（不在 `ALL_STATES`，
无任何分支）。

### 事件（`KNOWN_EVENTS`，`legacy_order_machine.py:26-41`，共 14 个）

`pay`、`cancel`、`timeout`、`stock_ok`、`stock_out`、`pick_done`、`ship`、
`deliver`、`close`、`return_request`、`refund_approve`、`refund_done`、
`audit_pass`、`audit_fail`

- 文档有、代码没有：`flag_risk`（任何状态下抛 `UnknownEventError`）
- 代码有、文档转移表没用到：`audit_pass`/`audit_fail`（只在死代码
  AUDITING 分支里被接受）
- 代码分支里出现但从未注册：`migrate`（`legacy_order_machine.py:240`，
  在第 0 层校验 `:137-140` 就被 `UnknownEventError` 挡下）

---

## 二、完整状态转移表（实现版，逐条标注可达性）

行为分类：`转移`=状态改变并记 history（`_go` `:93-98`）；
`自循环`=状态不变但记 history（`_stay` `:100-103`）；
`静默忽略`=状态不变、不进 history、只记 ignored 账本（`_ignore` `:105-108`）；
`抛错`=状态不变，抛异常（`_illegal` `:110-115` / 第 0 层 `:137-140`）。

| # | 当前状态 | 事件 | 结果 | 行为 | 可达性 | 代码位置 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | CREATED | pay | → PAID | 转移 | ✅ 可达 | `fire()` `:152-157` |
| 2 | CREATED | pay（amount≤0） | 抛 InvalidTransitionError | 抛错 | ✅ 可达 | `:154-156` |
| 3 | CREATED | cancel | → CANCELLED | 转移 | ✅ 可达 | `:158-160` |
| 4 | CREATED | timeout | → CANCELLED | 转移 | ✅ 可达 | `:161-163` |
| 5 | PAID | stock_ok | → PICKING | 转移 | ✅ 可达 | `:170-171` |
| 6 | PAID | stock_out | → REFUNDING | 转移 | ✅ 可达（文档说留在 PAID） | `:172-174` |
| 7 | PAID | cancel | → REFUNDING | 转移 | ✅ 可达（文档说 →CANCELLED） | `:175-177` |
| 8 | PAID | timeout | 留在 PAID | 自循环 | ✅ 可达（文档未记载） | `:178-180` |
| 9 | PICKING | pick_done | → PACKED | 转移 | ✅ 可达 | `:187-188` |
| 10 | PICKING | stock_out | → REFUNDING | 转移 | ✅ 可达（文档未记载） | `:189-191` |
| 11 | PICKING | cancel | → REFUNDING | 转移 | ✅ 可达（文档说 →CANCELLED） | `:192-194` |
| 12 | PACKED | ship | → SHIPPED | 转移 | ✅ 可达 | `:201-202` |
| 13 | PACKED | cancel | → REFUNDING | 转移 | ✅ 可达（文档说 →CANCELLED） | `:203-204` |
| 14 | SHIPPED | deliver | → DELIVERED | 转移 | ✅ 可达 | `:211-212` |
| 15 | SHIPPED | timeout（第 1、2 次） | 留在 SHIPPED | 自循环 | ✅ 可达（文档说立即签收） | `:213-219` |
| 16 | SHIPPED | timeout（第 3 次） | → DELIVERED | 转移 | ✅ 可达 | `:217-218`，阈值 `MAX_SHIP_TIMEOUTS=3` `:63` |
| 17 | SHIPPED | cancel | 留在 SHIPPED | 静默忽略 | ✅ 可达（文档说报错） | `:220-222` |
| 18 | SHIPPED | pay | 留在 SHIPPED | 静默忽略 | ✅ 可达（文档未记载） | `:223-225` |
| 19 | DELIVERED | close | → CLOSED | 转移 | ✅ 可达 | `:232-233` |
| 20 | DELIVERED | deliver | 留在 DELIVERED | 静默忽略 | ✅ 可达（文档未记载） | `:234-236` |
| 21 | DELIVERED | return_request | （→ REFUNDING） | 转移 | 💀 **死代码**：`_legacy_v1_returns` 恒为 False（`:88`），运行时走到 `:244` 抛错 | `:237-239` |
| 22 | DELIVERED | migrate | （→ ARCHIVED） | 转移 | 💀 **死代码**：`migrate` 不在 `KNOWN_EVENTS`，第 0 层 `:137-140` 先抛 UnknownEventError | `:240-243` |
| 23 | REFUNDING | refund_done | → REFUNDED | 转移 | ✅ 可达 | `:250-251` |
| 24 | REFUNDING | refund_approve | 抛 InvalidTransitionError | 抛错（显式拒绝） | ✅ 可达（文档说 →REFUND_APPROVED） | `:252-255` |
| 25 | AUDITING | audit_pass | → CLOSED | 转移 | 💀 **死代码**：AUDITING 状态不可达 | `:266-267` |
| 26 | AUDITING | audit_fail | → CANCELLED | 转移 | 💀 **死代码**：同上 | `:268-269` |
| 27 | CLOSED/CANCELLED/REFUNDED | 任意已知事件 | 留在原状态 | 静默忽略 | ✅ 可达（文档说抛错） | 终态拦截 `:143-146` |
| 28 | 任意状态 | 未注册事件 | 抛 UnknownEventError | 抛错 | ✅ 可达 | 第 0 层校验 `:137-140` |
| 29 | 未知状态（如 ARCHIVED） | 任意已知事件 | 抛 InvalidTransitionError | 抛错 | 仅篡改 `.state` 可触发 | 防御兜底 `:273-276` |

### 死代码汇总（4 个分支 + 2 个状态）

| 死代码 | 位置 | 死因 |
| --- | --- | --- |
| `DELIVERED return_request → REFUNDING` | `:237-239` | 开关 `_legacy_v1_returns` 在 `:88` 恒为 False，全文件无第二处赋值 |
| `DELIVERED migrate → ARCHIVED` | `:240-243` | `migrate` 不在 `KNOWN_EVENTS`（`:26-41`），永远过不了第 0 层 |
| `AUDITING` 整个状态块（2 个分支） | `:265-270` | 无任何已知事件能进入 AUDITING（`flag_risk` 已移除） |
| 状态 `AUDITING`、`ARCHIVED` | `:58-59` | 仅存在于 `ALL_STATES` 常量，运行时不可达（BFS 实测） |
| 文档状态 `REFUND_APPROVED` | 文档 §一 | 代码中不存在，属文档侧"幽灵状态" |

---

## 三、实现与文档的差异（每条均有可复现实验，见 `experiments/02_doc_diff.py`）

| # | 文档说 | 实测结果 | 证据位置 | 实验 |
| --- | --- | --- | --- | --- |
| D1 | PAID --stock_out--> PAID（等待补货） | → **REFUNDING**（2021-03 起直接退款） | `:172-174` | d1 |
| D2 | PAID --cancel--> CANCELLED | → **REFUNDING**（已付款须先退款） | `:175-177` | d2 |
| D3 | PICKING --cancel--> CANCELLED | → **REFUNDING** | `:192-194` | d3 |
| D4 | PACKED --cancel--> CANCELLED | → **REFUNDING** | `:203-204` | d4 |
| D5 | SHIPPED --timeout--> DELIVERED（超时立即签收） | 需**连续 3 次** timeout 才签收，前 2 次自循环 | `:213-219`、`:63` | d5 |
| D6 | SHIPPED --cancel--> 报错 | **静默忽略**（记 ignored 账本，不抛错） | `:220-222` | d6 |
| D7 | DELIVERED --return_request--> REFUNDING | 抛 **InvalidTransitionError**（v1 退货入口已死） | `:237-239`、`:88` | d7 |
| D8 | CREATED --flag_risk--> AUDITING | 抛 **UnknownEventError**（事件已注销） | `:26-41`、`:137-140` | d8 |
| D9 | AUDITING --audit_pass/audit_fail--> CLOSED/CANCELLED | 状态不可达；在 CREATED 发 audit_pass 抛 InvalidTransitionError | `:265-270` | d9 |
| D10 | REFUNDING --refund_approve--> REFUND_APPROVED | 抛 **InvalidTransitionError**（2022-11 起自动审批，显式拒绝旧事件） | `:252-255` | d10 |
| D11 | 状态清单含 REFUND_APPROVED | 代码中**不存在**该状态 | `:47-60` | d11 |
| D12 | （文档未记载） | 实现多出：PAID timeout 自循环、SHIPPED 重复 pay 忽略、DELIVERED 重复 deliver 忽略 | `:178-180`、`:223-225`、`:234-236` | d12 |
| D13 | （文档未记载） | CREATED 的 pay 带 `amount<=0` 会被拒绝（抛 InvalidTransitionError） | `:154-156` | d13 |
| D14 | （文档未记载） | DELIVERED 有 migrate→ARCHIVED 归档分支（死代码） | `:240-243` | d14 |
| D15 | §三：任何状态下未列出的事件一律抛 InvalidTransitionError | **终态例外**：CLOSED/CANCELLED/REFUNDED 对全部 14 个已知事件静默忽略 | `:143-146` | 实验 3 |

实验 2 全部场景原始输出：`experiments/results/02_doc_diff.json`。

---

## 四、非法事件处理逐项结论（实验 3，穷举 12 状态 × 17 事件）

总规则（实测确认）：

1. **未知事件永远最先抛 `UnknownEventError`**（`:137-140`），包括终态在内——
   终态拦截在 `:143`，排在第 0 层校验之后。
2. **终态（CLOSED/CANCELLED/REFUNDED）对全部 14 个已知事件静默忽略**
   （`_ignore`，`:143-146`），不抛错、不改状态、只记 `ignored` 账本。
   这与文档 §三"一律抛 InvalidTransitionError"矛盾（差异 D15）。
3. 非终态下"已知但不接受"的事件**抛 `InvalidTransitionError`**，除了
   三处显式静默忽略（SHIPPED 的 cancel/pay、DELIVERED 的 deliver）。

| 状态 | 接受的已知事件 | 静默忽略 | 抛 InvalidTransitionError | 抛 UnknownEventError |
| --- | --- | --- | --- | --- |
| CREATED | pay、cancel、timeout | 无 | 其余 11 个已知事件 | 所有未注册事件 |
| PAID | stock_ok、stock_out、cancel、timeout(自循环) | 无 | 其余 10 个已知事件 | 所有未注册事件 |
| PICKING | pick_done、stock_out、cancel | 无 | 其余 11 个已知事件 | 所有未注册事件 |
| PACKED | ship、cancel | 无 | 其余 12 个已知事件 | 所有未注册事件 |
| SHIPPED | deliver、timeout(计数) | **cancel、pay** | 其余 10 个已知事件 | 所有未注册事件 |
| DELIVERED | close | **deliver** | 其余 12 个已知事件（含 return_request） | 所有未注册事件 |
| REFUNDING | refund_done | 无 | 其余 13 个已知事件（refund_approve 为显式拒绝 `:252-255`） | 所有未注册事件 |
| CLOSED（终态） | 无 | **全部 14 个已知事件** | 无 | 所有未注册事件 |
| CANCELLED（终态） | 无 | **全部 14 个已知事件** | 无 | 所有未注册事件 |
| REFUNDED（终态） | 无 | **全部 14 个已知事件** | 无 | 所有未注册事件 |
| AUDITING（不可达，篡改 `.state` 探测） | audit_pass、audit_fail | 无 | 其余 12 个已知事件 | 所有未注册事件 |
| ARCHIVED（不可达，篡改 `.state` 探测） | 无 | 无 | 全部 14 个已知事件（兜底 `:273-276`） | 所有未注册事件 |

完整矩阵：`experiments/results/03_illegal.json`。

---

## 五、改造前须知（给后续修改者的风险提示）

- 改 `PAID/PICKING/PACKED` 的 `cancel` 时注意：它们现在都去 `REFUNDING`，
  不是文档说的 `CANCELLED`；财务流程依赖此行为（`:176` 注释）。
- `SHIPPED` 的 `timeout` 有计数器副作用（`ship_timeouts`，`:85`/`:216`），
  重放事件会推进计数，不是幂等的。
- 静默忽略不是"没发生"：`ignored` 账本（`:83`、`:107`）会累积，
  排查重复回调问题时要查它而不是 `history`。
- 删死代码（#21/#22/#25/#26）本身是安全的（运行时不可达），但
  `ALL_STATES` 里的 `AUDITING`/`ARCHIVED` 可能被外部报表引用，删除前先全局搜索。
