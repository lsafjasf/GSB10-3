# 字素簇切分与按簇光标操作

按**用户感知的字符**（grapheme cluster，UAX #29）移动光标和删除，
避免把组合字符、ZWJ 表情序列、变体选择符、旗标拆成碎片。
纯 Node.js 标准库实现（`Intl.Segmenter`），无第三方依赖，要求 Node.js ≥ 16。

## 文件

- `grapheme.js` — 库：切分 + 光标/编辑器操作
- `selftest.js` — 自测：20 项断言（切分用例、操作状态序列、随机往返、边界用例）
- `demo.js` — 演示：打印逐次操作后的文本状态序列

## 运行

```sh
node selftest.js   # 跑全部自测，全部通过时退出码为 0
node demo.js       # 查看光标移动/删除的状态序列演示
```

## 库 API

| 函数 | 说明 |
| --- | --- |
| `segment(text)` | 正向切分为簇数组（基于 `Intl.Segmenter`） |
| `boundaries(text)` | 全部簇边界（码元偏移，含 0 与末尾） |
| `nextBoundary(text, pos)` / `prevBoundary(text, pos)` | 下一个/上一个簇边界 |
| `segmentForward(text)` / `segmentBackward(text)` | 用边界步进实现的正向/反向逐簇切分 |
| `new Editor(text)` | 迷你编辑器，光标恒在簇边界上 |
| `moveLeft/moveRight/moveToStart/moveToEnd` | 按簇移动光标 |
| `backspace()` / `deleteForward()` | 删除光标左/右侧的整个簇 |
| `insert(s)` | 插入文本，光标归一到最近簇边界 |
| `visualize(text, cursor)` | 用 `▏`（光标）、`|`（簇边界）、`<U+XXXX>`（不可见字符）渲染状态 |

## 覆盖的切分规则

- 组合字符（GB9）：`e + U+0301`、叠多个组合符、天城文 SpacingMark（GB9a）
- ZWJ 序列（GB11）：`👨‍👩‍👧‍👦`、`🏳️‍🌈`；肤色修饰符 `👍🏽`
- 变体选择符：`✈️`（U+FE0F）、`邊󠄀`（U+E0100 IVS）
- 区域指示符（GB12/13）：`🇨🇳` 一簇；奇数个 RI 时前两个成旗、末个落单
- `CR LF` 一簇（GB3）；双向控制符（RLO/PDF/LRI…）各自成簇（GB4/GB5）

## 关键性质（自测中断言）

1. **往返一致**：随机文本上 `segmentBackward(t).reverse()` 与 `segmentForward(t)`
   逐簇相等，且两者拼接都还原原文（1000 轮，含组合符/ZWJ/RI/双向控制符/孤立代理项）。
2. **路径一致**：光标从尾往左走与从头往右走经过完全相同的边界序列。
3. **删除完整性**：从末尾连续退格，每步恰好删掉末尾一个完整的簇，
   剩余前缀的切分不变（UAX #29 的规则只依赖左侧上下文）。
4. **光标不变量**：光标永远是簇边界，绝不落在簇内或代理对中间；
   插入导致合并时（如在孤立组合符前插入字母）光标归一到簇尾。

## 说明

- 光标位置用 UTF-16 码元偏移表示，与 JS 字符串下标一致；
  与按 UTF-8 字节计费的系统对接时需在边界处换算。
- `Intl.Segmenter` 的 `und` 区域设置即默认的 UAX #29 字素簇规则，
  与具体语言无关，适合编辑器场景。
