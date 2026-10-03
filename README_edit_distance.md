# 编辑距离与近似匹配（Python 3，仅标准库）

## 文件

- `edit_distance.py` —— 库：Damerau–Levenshtein 距离（OSA 变体）+ 阈值限界计算 + 近似子串匹配
- `test_edit_distance.py` —— 单元测试与边界用例
- `benchmark.py` —— 提前终止 vs 完整计算的耗时/单元数对比
- `demo_search.py` —— 20 万字符长文本中定位相似片段，输出命中位置清单
- `benchmark_output.txt` / `demo_output.txt` —— 上述两个脚本的一次实际运行输出快照

## 运行方式

```bash
python3 test_edit_distance.py   # 自测（17 个用例）
python3 benchmark.py            # 阈值对比数据
python3 demo_search.py          # 命中位置清单
```

## 四类操作与可配置代价

`Costs(insert, delete, substitute, transpose)`，默认均为 1：

| 操作 | 字段 | 含义 | 默认代价 |
|------|------|------|----------|
| 插入 | `insert` | 在 a 中插入一个字符 | 1 |
| 删除 | `delete` | 从 a 中删除一个字符 | 1 |
| 替换 | `substitute` | 把一个字符换成另一个 | 1 |
| 相邻交换 | `transpose` | 交换两个相邻字符（如 ab→ba） | 1 |

说明：

- 交换采用 Optimal String Alignment 变体，代价与其他操作独立配置；例如把
  `transpose` 设为 5，`ab→ba` 的结果就是 2（两次替换），而非 1。
- 算法始终选择代价最小的操作组合，例如 `substitute=9` 时 `a→b` 会走“删除+插入”=2。
- 代价必须为非负数；`min(insert, delete)` 为 0 时不做带状裁剪（保证正确）。

## 阈值提前终止的原理

`edit_distance_bounded(a, b, threshold)` 使用两层剪枝：

1. **带状裁剪**：到达单元 (i,j) 至少需要 |i-j| 次插入或删除，
   故 `d(i,j) >= |i-j| * min(insert, delete)`。带状区域外的单元一律跳过，
   带宽 = `threshold / min(insert, delete)`。
2. **双行最小值剪枝**：交换一步跨两行，路径可能跳过某一行但不可能连续跳过两行；
   因此连续两行的最小值都超过 threshold 时，最终距离必然超过阈值，立即终止。

返回 `BoundedResult(distance, exceeded, cells)`：未超阈值时 `distance` 与完整
计算完全一致；超阈值时 `exceeded=True` 并提前结束。`cells` 为实际计算的 DP
单元数，用来量化“少算了多少”。

## 相似度定义

`find_similar` 接受 `max_distance`（距离阈值）或 `min_similarity`（相似度阈值）：

```
similarity = 1 - distance / (max(|pattern|, |命中片段|) * 单位代价)
单位代价 = 四类操作代价的最大值
```

近似子串匹配采用“第一行全 0”的经典 DP（匹配可从文本任意位置开始），
并在每个单元回溯记录片段起点；同一命中的多个相邻终点贪心去重。
复杂度 O(|pattern| × |text|)，空间 O(|text|)。

## 边界用例（见 test_edit_distance.py）

- 空串：双方皆空、仅一方为空、`find_similar` 空模式串抛 `ValueError`
- 完全相同：含 500 字符长串
- 只差一个相邻交换：`ab/ba`、`abcd/acbd`、`kitten/kittne`
- 自定义代价：四类操作逐一验证、交换代价退化为两次替换
- 超长文本：2 万字符（植入命中）与 10 万字符（冒烟）
- 限界与完整结果一致性：300 组随机串 × 6 档阈值 + 100 组自定义代价随机串
