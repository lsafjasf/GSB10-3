# 可持久化区间树（Persistent Segment Tree）

Python 3 纯标准库实现。支持任意历史版本回退与区间查询，
每次单点更新只克隆根到叶子的路径（O(log n) 个新节点），
历史版本之间共享其余节点，互不干扰。

## 文件

- `persistent_range_tree.py` — 库（无第三方依赖）
- `selftest_persistent_range_tree.py` — 自测 + 节点增长实测

## 运行

```bash
cd persistent_range_tree
python3 selftest_persistent_range_tree.py
```

## 用法

```python
from persistent_range_tree import PersistentRangeTree

tree = PersistentRangeTree(100)          # 下标域 [0, 100)，初始全零
v1 = tree.update(0, 5, 10)               # 基于版本 0 把位置 5 设为 10
v2 = tree.update(v1, 20, -3)             # 基于 v1 继续更新
s, mn, mx = tree.query(v1, 0, 100)       # (10, 0, 10) —— 看不到 v2 的修改
s, mn, mx = tree.query(v2, 0, 100)       # (7, -3, 10)
tree.query(v2, 3, 3)                     # 空区间 -> (0, +inf, -inf)
```

- 区间一律为半开区间 `[left, right)`。
- `update(version, pos, value)` 返回新版本号，旧版本永不改变。
- `query(version, left, right)` 返回 `(sum, min, max)`；
  空区间返回单位元 `(0, +inf, -inf)`。
- 值支持 `int` / `float`（任何支持 `+`、`min`、`max` 的类型）。

## 复杂度

| 操作 | 时间 | 空间 |
|---|---|---|
| 单点更新 | O(log n) | 新增 O(log n) 个节点 |
| 区间查询 | O(log n) | 不新增节点 |
| 总节点数 | — | 1 + 更新次数 × (log2 n + 1) |

## 节点增长实测（自测脚本输出）

下标域 n = 131072（2^17），树高 18，每次更新恰好新增 18 个节点：

| 更新次数 | 累计新增节点 | 平均每次新增 | 相对整树复制节省 |
|---|---|---|---|
| 100 | 1,800 | 18.00 | 7281.8x |
| 1,000 | 18,000 | 18.00 | 7281.8x |
| 10,000 | 180,000 | 18.00 | 7281.8x |
| 20,000 | 360,000 | 18.00 | 7281.8x |

非二次幂域 n = 99991：路径长度只有 17 或 18 两种
（floor/ceil(log2 n) + 1），抽样 64 次平均 17.48 个节点/次更新。

对比整树复制：20,000 次更新需要 20,000 × 131,072 ≈ 26.2 亿个节点，
可持久化版本只需 36 万个，节省约 7282 倍。

## 自测覆盖

- 基础单点更新 + 区间查询，与逐元素计算逐一对照
- 版本独立性：链式历史回查、同一版本分叉互不影响、旧根不被复用
- 大量历史版本：3,000 个版本 / 域大小 99,991 的随机压力测试
- 边界用例：空区间单位元、单元素域、size=0、越界参数、浮点值
- 节点增长：二次幂域恒定增量断言 + 非二次幂域路径长度分布断言
