# -*- coding: utf-8 -*-
"""
字典序最小的拓扑排序（Kahn + 最小堆），纯标准库实现。

依赖约定：边 (u, v) 表示 u 必须排在 v 前面（u -> v）。

贪心为什么成立（交换论证）：
    Kahn 算法每一步只能选择当前入度为 0 的候选节点。设所有候选中标签最小的
    是 m。任取一个合法拓扑序 O，设 O 的第一个节点是 x：
      - 若 x == m，无需处理；
      - 若 x != m，则 m 在 O 中位于 x 之后。m 是候选意味着没有任何“尚未输出”
        的节点指向 m，而 O 中排在 m 前面的节点（含 x）都属于尚未输出集合，
        所以它们与 m 之间不存在指向 m 的边；把 m 直接移到 O 的最前面不会违反
        任何边约束（m 指向的节点仍在它后面）。
    因此总能构造出一个以 m 开头的合法序。第一步选 m 后，问题缩小为剩余图上的
    同类问题，归纳即得：每步取最小候选，得到的就是字典序最小的拓扑序。
    用普通队列只能得到“某个”拓扑序；用最小堆（heapq）才能稳定取到最小候选。
"""

import heapq
from collections import defaultdict, deque


class CycleError(ValueError):
    """图中存在环时抛出；cycle 为按环顺序排列的节点列表。"""

    def __init__(self, cycle):
        self.cycle = list(cycle)
        self.cycle_nodes = frozenset(cycle)
        super().__init__("检测到环，环上节点: %s" % " -> ".join(map(repr, self.cycle)))


def _build_graph(nodes, edges):
    node_set = set(nodes)
    adj = defaultdict(set)
    indeg = {n: 0 for n in node_set}
    for u, v in edges:
        if u not in node_set or v not in node_set:
            raise ValueError("边引用了未声明的节点: %r -> %r" % (u, v))
        if v not in adj[u]:          # 去重，保证重复边只计一次入度
            adj[u].add(v)
            indeg[v] += 1
    return node_set, adj, indeg


def topological_sort(nodes, edges):
    """
    返回字典序最小的拓扑序列表；有环时抛出 CycleError（带环上节点）。

    nodes: 可比较、可哈希的标签（如 str/int），按其自然顺序比较字典序。
    edges: 可迭代的 (u, v)，语义为 u 必须先于 v。
    """
    node_set, adj, indeg = _build_graph(nodes, edges)

    heap = [n for n, d in indeg.items() if d == 0]
    heapq.heapify(heap)             # O(k) 建堆，之后 heappop 取最小候选

    order = []
    while heap:
        u = heapq.heappop(heap)
        order.append(u)
        for v in sorted(adj[u]):    # 排序仅为输出确定；入堆后仍由堆保证最小
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(heap, v)

    if len(order) == len(node_set):
        return order

    # 有节点残留 => 存在环。残留子图中每个节点入度都 >= 1，
    # 沿“残留前驱”一路回退必然重复，重复段即一条真实环。
    remaining = node_set - set(order)
    rev = defaultdict(list)
    for u in remaining:
        for v in adj[u]:
            if v in remaining:
                rev[v].append(u)

    start = next(iter(remaining))
    path = [start]
    pos = {start: 0}
    cur = start
    while True:
        cur = rev[cur][0]
        if cur in pos:
            # 回退沿的是反向边，翻转后才是沿真实边方向的环
            segment = path[pos[cur]:]
            cycle = segment[::-1] + [segment[-1]]
            raise CycleError(cycle)
        pos[cur] = len(path)
        path.append(cur)


def validate_order(order, edges):
    """
    逐边校验：对每条边 (u, v) 断言 u 在序中严格位于 v 之前。
    同时校验 order 本身是节点的一个无重复排列。校验失败抛 AssertionError。
    """
    position = {}
    for i, n in enumerate(order):
        assert n not in position, "排序结果含重复节点: %r" % n
        position[n] = i

    checked = 0
    for u, v in edges:
        assert u in position and v in position, "排序缺少边端点: %r -> %r" % (u, v)
        assert position[u] < position[v], (
            "违反依赖边: %r 必须先于 %r，实际位置 %d >= %d"
            % (u, v, position[u], position[v])
        )
        checked += 1
    assert len(position) == len(order)
    return checked  # 返回已校验的边数，便于测试断言


# ---------------------------------------------------------------------------
# 自测：无第三方框架，直接 `python3 topo_sort.py` 运行
# ---------------------------------------------------------------------------

def _selftest():
    # 1) 链式依赖：a -> b -> c
    chain_nodes = ["a", "b", "c"]
    chain_edges = [("a", "b"), ("b", "c")]
    order = topological_sort(chain_nodes, chain_edges)
    assert order == ["a", "b", "c"], order
    assert validate_order(order, chain_edges) == 2

    # 2) 大量并列候选：无任何边，乱序输入也必须得到字典序结果
    import random
    rng = random.Random(42)
    many = list(range(1000))
    rng.shuffle(many)
    order = topological_sort(many, [])
    assert order == list(range(1000)), "并列候选未按字典序输出"
    assert validate_order(order, []) == 0

    # 3) 堆选择真正起作用：a、b 同时就绪必须先取 a；c 释放后再比较
    #    边: a->c, b->c, c->d，另加独立大标签 z
    edges3 = [("a", "c"), ("b", "c"), ("c", "d")]
    order = topological_sort(["d", "c", "b", "a", "z"], edges3)
    assert order == ["a", "b", "c", "d", "z"], order
    assert validate_order(order, edges3) == 3

    # 4) 含环：a -> b -> c -> a，且有外挂尾巴 d -> a；报错必须给出环上节点
    try:
        topological_sort(["a", "b", "c", "d"], [("a", "b"), ("b", "c"),
                                                ("c", "a"), ("d", "a")])
    except CycleError as e:
        assert e.cycle_nodes == frozenset(["a", "b", "c"]), e.cycle_nodes
        assert "d" not in e.cycle_nodes, "外挂节点不应被误报为环上节点"
        # 报告的环本身必须逐边真实存在
        for x, y in zip(e.cycle, e.cycle[1:]):
            assert (x, y) in {("a", "b"), ("b", "c"), ("c", "a")}, (x, y)
        print("  环检测样例 ->", e)
    else:
        raise AssertionError("含环图未抛出 CycleError")

    # 5) 单节点
    order = topological_sort(["only"], [])
    assert order == ["only"]
    assert validate_order(order, []) == 0

    # 边界：空图
    assert topological_sort([], []) == []

    # 边界：自环
    try:
        topological_sort(["x"], [("x", "x")])
    except CycleError as e:
        assert e.cycle_nodes == frozenset(["x"])
    else:
        raise AssertionError("自环未被检测")

    # 边界：菱形依赖且重复边/乱序输入不影响结果
    diamond = [("c", "d"), ("a", "b"), ("a", "c"), ("a", "c")]  # 末条重复
    order = topological_sort(["d", "c", "b", "a"], diamond)
    assert order == ["a", "b", "c", "d"], order
    assert validate_order(order, diamond) == 4  # 逐边都校验（含重复边）

    # 边界：边引用未声明节点必须明确报错
    try:
        topological_sort(["a"], [("a", "ghost")])
    except ValueError:
        pass
    else:
        raise AssertionError("未声明节点未被拦截")

    # 边界：错误排序必须被逐边校验拦下
    bad_order = ["c", "a", "b"]
    try:
        validate_order(bad_order, [("a", "b"), ("b", "c")])
    except AssertionError:
        pass
    else:
        raise AssertionError("非法排序未被校验拦下")

    # 6) 稍大的随机有向无环图：反复随机生成，逐边校验且验证字典序最优性
    #    最优性检验：结果中每个位置都必须是当时合法可选的最小标签，
    #    这里直接用独立的朴素 Kahn（每轮全表扫最小）对照。
    def naive_lex_topo(ns, es):
        adj2 = defaultdict(set)
        indeg2 = {n: 0 for n in ns}
        for u, v in es:
            if v not in adj2[u]:
                adj2[u].add(v)
                indeg2[v] += 1
        out = []
        ready = [n for n in ns if indeg2[n] == 0]
        while ready:
            pick = min(ready)       # 每轮全表扫最小，与堆实现相互独立
            ready.remove(pick)
            out.append(pick)
            for v in adj2[pick]:
                indeg2[v] -= 1
                if indeg2[v] == 0:
                    ready.append(v)
        return out if len(out) == len(ns) else None

    for trial in range(200):
        n = rng.randint(1, 12)
        ns = ["n%02d" % i for i in range(n)]
        es = []
        # 只允许小编号 -> 大编号，保证无环
        for i in range(n):
            for j in range(i + 1, n):
                if rng.random() < 0.25:
                    es.append((ns[i], ns[j]))
        got = topological_sort(list(ns), es)
        assert validate_order(got, es) == len(es)
        assert got == naive_lex_topo(ns, es), (trial, got)

    print("全部自测通过：链式 / 千个并列候选 / 含环(含自环) / 单节点 / "
          "空图 / 菱形重复边 / 非法节点 / 非法排序 / 200 组随机 DAG 对照")


if __name__ == "__main__":
    _selftest()
