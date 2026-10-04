# 不可达组合与冗余/遗漏分析

穷举 2592 个组合后，以下组合无法产生"独特"行为，或对应原实现中的死代码。
分类：**逻辑冗余**（代码写了但永远不会执行/执行了也无差别）与**遗漏**（疑似需求缺口，行为保留并标注）。

## 逻辑冗余

- **U1 overseas + express（64 组合）**：R23 恒抛 RuntimeError，其下所有费用/折扣分支不可达。
  原实现 staff 折扣区里 `overseas and express → fee*0.5` 是死代码，重构版已删除。
- **U2 frozen 账号 + express（192 组合）**：R11 恒抛 RuntimeError。
  原实现 frozen 分支末尾 `if holiday: if express: fee += 5` 的 express 内层是死代码，已删除。
- **U3 gold 折扣的三段重复**：原实现对 domestic/remote/overseas 各写一遍 `fee*0.90`，
  三个分支完全相同，重构版合并为一条 10% 折扣规则。
- **U4 banned 账号的其余条件（576 组合）**：R03 之后所有条件不影响输出，
  决策表中这些组合全部折叠为一条规则。

## 遗漏（行为保留，待需求确认）

- **G1 amount == 0 先于封号检查**：banned 账号提交 0 元订单会返回 0.0 而非 PermissionError。
  疑似安全漏洞，但为保持回归一致，重构版在 R01 原样保留并显式标注。
- **G2 silver 折扣仅 domestic 生效**：remote/overseas 的 silver 用户无折扣，
  原实现该分支为空 fallthrough，疑似需求遗漏。
- **G3 未知 user_level 静默无折扣**：原实现未校验 level，未知值按无折扣处理，
  重构版保留该行为（`TestErrorCombinations` 中有对应用例锁定）。

## 说明

"不可达"指：给定前置守卫后，该组合与某些更早命中的组合输出完全相同，
或对应源码分支在控制流上不可达。U1/U2 的死代码删除不改变任何可达行为，
已由 `tools/diff_test.py` 的 2592 组全量对拍验证（0 不一致）。
