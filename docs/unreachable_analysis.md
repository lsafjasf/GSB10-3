# 不可达组合 / 分支分析

对 `src/legacy_pricing.py`（11 层嵌套）做全组合穷举（864 个条件组合 +
29952 个边界点 + 5000 个随机用例 + 15 个非法输入）并进行行级覆盖追踪，
结论如下。覆盖证据由 `build_decision_table.py` 与
`tests/test_regression.py::TestStructure::test_legacy_dead_lines_never_hit_rest_covered`
自动复核：被标注的死语句命中数必须为 0，其余语句必须 100% 覆盖。

## 一、逻辑冗余（redundant）—— 死分支，共 6 处

外层条件已经蕴含内层判断的结果，内层某一分支永远无法进入。
属于**逻辑冗余**（代码可以删，但没人敢删）。

| # | 位置 | 死语句 | 不可达原因 |
|---|------|--------|-----------|
| 1 | gold / 大额 / 无券 / 非生日 | `label = "gold-big-none-small"` | 外层 `if qty >= 10` 与内层重复，内层 else 恒不可达 |
| 2 | 会员 else 链末端 | `label = "unknown-vip"` | 外层 `else` 已保证 `vip == "normal"`，内层再判恒真 |
| 3 | 批量折扣 normal 档 | `rate -= 1` | normal 的 rate 恒为 95，`rate < 90` 恒假 |
| 4 | 最深路径 xl 档 | `label = "gold-xl-no-bday"` | 外层已保证 `birthday` 为真，冗余复查的 else 不可达 |
| 5 | 最深路径 xl 档 | `label = "gold-xl-not-gold"` | 外层已保证 `vip == "gold"`，冗余复查的 else 不可达 |
| 6 | 最深路径 xl 档 | `label = "gold-xl-low-amount"` | 外层已保证 `amount >= 20000`，冗余复查的 else 不可达 |

处理方式：表驱动实现中不为这些分支建表项；
`TestDocumentedQuirks::test_dead_label_never_returned` 固化
"这些标签永远不会出现在输出中"这一可观测事实。

## 二、遗漏（omission）—— 缺失分支，共 1 处

| # | 位置 | 现象 | 分析 |
|---|------|------|------|
| 1 | 运费计算 `channel == "web"` 且 `region == "remote"` | 无任何运费规则，静默沿用初始值 `shipping = 0` | 对照 `app+remote`（1200/1800）与 `web+mainland`（0/800）可知此处**应为遗漏而非有意免费**：同一函数内其余 3 个 channel×region 组合都有规则，唯独此组合缺失。疑似历史缺陷。 |

处理方式：**按原样保留**（`SHIPPING_TABLE[("web", "remote")] = (0, 0)`），
因为下游报表可能已依赖该行为；在决策表与本文件中显式标注，
由 `TestDocumentedQuirks::test_web_remote_shipping_omission` 固化。
是否修复属于业务决策，不在本次重构范围内。

## 三、关于"不可达组合"的说明

864 个条件组合在**合法输入域**内全部可达（每个组合的代表值都能驱动
原实现走到对应路径），不存在输入域内的不可达组合。真正的不可达性
全部体现为上述"组合可达、但组合内部的某条子分支不可达"，
即冗余死分支；以及"组合存在、但实现没有为它写分支"，即遗漏。
两类均已标注并自动化复核。
