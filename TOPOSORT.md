# 字典序最小拓扑排序

实现位于 `toposort.py`，只依赖 Python 3 标准库。

## API

```python
from toposort import (
    CyclicDependencyError,
    assert_topological_order,
    lexicographic_toposort,
)

nodes = ["fetch", "build", "test"]
edges = [("fetch", "build"), ("build", "test")]

order = lexicographic_toposort(nodes, edges)
assert_topological_order(nodes, edges, order)
```

边 `(before, after)` 表示 `before` 必须先于 `after` 构建。

- `lexicographic_toposort(nodes, edges)`：返回字典序最小的拓扑序。
- `assert_topological_order(nodes, edges, order)`：检查节点集合完整性，并逐边断言所有依赖方向均满足。
- `CyclicDependencyError`：存在环时抛出；`cycle_nodes` 给出环上的节点，`cycle_path` 给出首尾相同的闭合环路径。

## 贪心为什么成立

算法使用 Kahn 拓扑排序，但把“当前入度为 0 的候选集合”放进最小堆，每次取出字典序最小的候选。

证明要点：

1. 任意合法拓扑序的第一个节点，必须是当前图中入度为 0 的节点。
2. 设当前可候选节点中字典序最小的是 `m`。任取一个不以 `m` 开头的合法拓扑序，把序列后面的 `m` 移动到最前面：
   - `m` 当前入度为 0，所以不存在从前面节点指向 `m` 的边，移动不会违反入边；
   - `m` 移动后只会更早，因此也不会违反从 `m` 指出的边。
3. 交换后得到的新序列仍合法，且第一个元素更小，所以任何最优序列都可以替换为以 `m` 开头的序列。
4. 删除 `m` 后，剩余问题结构相同；对剩余子图重复同一选择，最终得到全局字典序最小的拓扑序。

最小堆正是用于在每一步以 `O(log V)` 的代价取出当前最小候选。总时间复杂度为 `O((V + E) log V)`，空间复杂度为 `O(V + E)`。

## 环检测

Kahn 算法结束后，如果仍有节点未输出，则剩余子图中每个节点都至少有一个来自剩余集合的前驱。有限有向图满足这个条件时必然存在有向环。

实现只在剩余子图上做 DFS；遇到当前 DFS 栈上的节点时，截取栈中一段并补回起点，得到闭合环路径。异常中的 `cycle_nodes` 只包含真正位于环上的节点，不包含仅被环阻塞的普通下游节点。

## 运行

运行自测：

```bash
python3 -m unittest -v test_toposort.py
```

运行内置示例：

```bash
python3 toposort.py
```

自测覆盖：

- 链式依赖；
- 单节点与空图；
- 1000 个并列候选；
- 最小堆相对 FIFO 的候选选择差异；
- 字符串前缀排序；
- 重复边；
- 有向环与自环；
- 与全排列暴力枚举结果对比；
- 逐边依赖校验断言及非法输入。
