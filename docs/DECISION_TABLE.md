# 决策表

由 `python3 tools/enumerate_table.py` 生成，完整数据见 `out/decision_table.csv`（一行一个组合）。

## 条件域

| 条件 | 取值 |
|---|---|
| `user_level` | normal / silver / gold / staff |
| `region` | domestic / remote / overseas |
| `category` | normal / fragile / frozen |
| `express` | False / True |
| `holiday` | False / True |
| `account_status` | active / frozen / banned |
| `amount` | -1 / 0 / 50 / 499.99 / 500 / 1000（覆盖 <0、=0、(0,500)、≥500 各区间及免邮边界） |

合计 4×3×3×2×2×3×6 = **2592 个组合**，原始实现共产生 **75 种不同输出**
（976 个正常报价，1616 个抛错组合）。

## 规则表（重构后 GUARDS 的顺序 = 原实现的真实优先级）

| 规则 | 条件 | 结果 | 命中组合数 |
|---|---|---|---|
| R01 | amount == 0 | 返回 0.0（早于封号检查，见 UNREACHABLE.md G1） | 432 |
| R02 | amount < 0 | ValueError | 432 |
| R03 | account_status == banned | PermissionError | 576 |
| R04 | account_status 未知 | ValueError | 0（域外输入，见边界测试） |
| R10 | frozen 且 region=overseas | PermissionError | 192 |
| R11 | frozen 且 express | RuntimeError | 192 |
| R12 | frozen 且 region 未知 | ValueError | 0（域外输入） |
| R13 | frozen 且 remote 且 category=frozen | ValueError | 32 |
| R14 | frozen 且 category 未知 | ValueError | 0（域外输入） |
| R20 | region 未知 | ValueError | 0（域外输入） |
| R21 | remote 且 category=frozen | ValueError | 64 |
| R22 | overseas 且 category=frozen | ValueError | 64 |
| R23 | overseas 且 express | RuntimeError | 64 |
| R24 | category 未知 | ValueError | 0（域外输入） |
| —— | 以上全部放行 | 进入费用表 | 544 |

## 费用表（守卫放行后）

- 基础运费 `BASE_FEE[(region, category)]`：domestic 6/14/18，remote 15/23，overseas 40/48（normal/fragile/frozen）。
- 免邮：active 且非 overseas 且 amount ≥ 500 → 基础运费归零（加急费另计）。
- 加急：domestic +10，remote +25；节假日加急再 +5（overseas 加急被 R23 拦截）。
- 折扣（仅 active）：silver 5%（仅 domestic）、gold 10%、staff 30% 且节假日再减 5；未知 level 无折扣。
- frozen 账号：仅基础运费，无免邮/折扣/加急。
- 结果下限 0，保留两位小数。

## 优先级要点（原实现只能靠读代码猜的部分）

1. `amount == 0` 先于一切，包括封号检查 → banned 账号下 0 元单也放行（疑似遗漏 G1）。
2. `amount < 0` 先于封号检查 → banned + 负数金额报 ValueError 而非 PermissionError。
3. frozen 账号：海外检查先于加急检查 → frozen+overseas+express 报 PermissionError。
4. overseas：冷冻品检查先于加急检查 → overseas+frozen+express 报 ValueError。
