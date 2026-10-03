"""二分图匹配库（仅标准库）。

功能：
1. 增广路径法（Kuhn 算法）求最大匹配，并记录每一轮增广后的匹配数量变化。
2. 匈牙利算法（Kuhn-Munkres）求最大权匹配。
3. 匹配合法性断言（无节点被重复匹配、边真实存在）。

约定：
- 二分图两侧节点集合为 U（左）与 V（右），用任意可哈希对象表示。
- 边以 (u, v) 二元组给出；多重边会被去重，不影响结果。
- 匹配用 dict 表示：{u: v}，键为左部节点，值为右部节点。
"""

from collections import defaultdict


# ---------------------------------------------------------------------------
# 工具：建图与合法性断言
# ---------------------------------------------------------------------------

def build_adjacency(left_nodes, right_nodes, edges):
    """根据边列表构建左部邻接表（多重边自动去重）。

    返回 (adj, edge_set)：
    - adj: dict, u -> [v, ...]，每个左部节点（含孤点）都有条目；
    - edge_set: set of (u, v)，用于合法性断言中校验匹配边真实存在。
    """
    left_set = set(left_nodes)
    right_set = set(right_nodes)
    adj = {u: [] for u in left_nodes}
    edge_set = set()
    for u, v in edges:
        if u not in left_set:
            raise ValueError(f"边的左端点 {u!r} 不在左部节点集合中")
        if v not in right_set:
            raise ValueError(f"边的右端点 {v!r} 不在右部节点集合中")
        if (u, v) in edge_set:  # 多重边去重
            continue
        edge_set.add((u, v))
        adj[u].append(v)
    return adj, edge_set


def assert_valid_matching(matching, edge_set, left_nodes=None, right_nodes=None):
    """断言匹配合法：无节点被重复匹配，且每条匹配边都真实存在。

    matching: dict {u: v}
    edge_set: 原图边集合 set((u, v), ...)
    """
    assert isinstance(matching, dict), "匹配必须表示为 {左节点: 右节点} 的 dict"
    used_right = set()
    for u, v in matching.items():
        if left_nodes is not None:
            assert u in set(left_nodes), f"左节点 {u!r} 不属于左部集合"
        if right_nodes is not None:
            assert v in set(right_nodes), f"右节点 {v!r} 不属于右部集合"
        assert (u, v) in edge_set, f"匹配边 ({u!r}, {v!r}) 在原图中不存在"
        assert v not in used_right, f"右节点 {v!r} 被重复匹配"
        used_right.add(v)
    return True


# ---------------------------------------------------------------------------
# 1. 增广路径法（Kuhn 算法）求最大匹配
# ---------------------------------------------------------------------------

def max_matching(left_nodes, right_nodes, edges):
    """增广路径法求最大匹配。

    返回 (matching, trace)：
    - matching: dict {u: v}，最大匹配；
    - trace: list of dict，每一轮增广的记录，字段：
        round        轮次（从 1 开始，每个左部节点一轮）
        left         本轮尝试匹配的左部节点
        augmented    本轮是否找到增广路
        size_before  本轮前的匹配数量
        size_after   本轮后的匹配数量
    """
    adj, edge_set = build_adjacency(left_nodes, right_nodes, edges)
    match_r = {}  # v -> u，右部节点的匹配对象
    trace = []

    def try_augment(u, seen):
        """从 u 出发 DFS 寻找增广路；找到则沿路翻转匹配并返回 True。"""
        for v in adj[u]:
            if v in seen:
                continue
            seen.add(v)
            if v not in match_r or try_augment(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    size = 0
    for round_no, u in enumerate(left_nodes, start=1):
        before = size
        augmented = try_augment(u, set())
        if augmented:
            size += 1
        trace.append({
            "round": round_no,
            "left": u,
            "augmented": augmented,
            "size_before": before,
            "size_after": size,
        })

    matching = {u: v for v, u in match_r.items()}
    assert_valid_matching(matching, edge_set, left_nodes, right_nodes)
    return matching, trace


# ---------------------------------------------------------------------------
# 2. 匈牙利算法（Kuhn-Munkres）求最大权匹配
# ---------------------------------------------------------------------------

def max_weight_matching(left_nodes, right_nodes, weighted_edges):
    """匈牙利算法求最大权匹配（允许不完美匹配，缺省边权视为 0）。

    weighted_edges: iterable of (u, v, w)；同一 (u, v) 出现多次时取最大权重。
    返回 (matching, total_weight)：
    - matching: dict {u: v}；
    - total_weight: 匹配总权重。

    实现要点：把两侧补齐成 n x n 方阵（n = max(|U|, |V|)），
    虚拟节点之间及不存在的边权为 0，因此算法可以自由地“不匹配”某节点，
    即求得的是全图上的最大权匹配（不要求完美匹配）。
    """
    # 聚合多重边：取最大权重
    weight = defaultdict(lambda: 0)
    explicit = set()
    left_set, right_set = set(left_nodes), set(right_nodes)
    for u, v, w in weighted_edges:
        if u not in left_set or v not in right_set:
            raise ValueError(f"边 ({u!r}, {v!r}) 的端点不在节点集合中")
        if (u, v) not in explicit or w > weight[(u, v)]:
            weight[(u, v)] = w
        explicit.add((u, v))

    U = list(left_nodes)
    V = list(right_nodes)
    n = max(len(U), len(V))
    # 权矩阵，行=左部（不足补虚拟行），列=右部（不足补虚拟列），缺省 0
    a = [[0] * (n + 1) for _ in range(n + 1)]  # 1-based
    for i, u in enumerate(U, start=1):
        for j, v in enumerate(V, start=1):
            a[i][j] = weight[(u, v)]

    # 经典 O(n^3) 匈牙利算法（1-based，求最大权完美匹配）
    lx = [0] * (n + 1)   # 左部顶标
    ly = [0] * (n + 1)   # 右部顶标
    match_y = [0] * (n + 1)  # 右部列 j 匹配的左部行
    for i in range(1, n + 1):
        lx[i] = max(a[i][1:])

    for i in range(1, n + 1):
        slack = [float("inf")] * (n + 1)
        prev = [0] * (n + 1)
        used = [False] * (n + 1)
        match_y[0] = i
        j0 = 0
        while True:
            used[j0] = True
            i0 = match_y[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = lx[i0] + ly[j] - a[i0][j]
                if cur < slack[j]:
                    slack[j] = cur
                    prev[j] = j0
                if slack[j] < delta:
                    delta = slack[j]
                    j1 = j
            for j in range(0, n + 1):
                if used[j]:
                    lx[match_y[j]] -= delta
                    ly[j] += delta
                else:
                    slack[j] -= delta
            j0 = j1
            if match_y[j0] == 0:
                break
        # 沿增广路翻转
        while j0:
            match_y[j0] = match_y[prev[j0]]
            j0 = prev[j0]

    # 提取结果：虚拟行列的匹配直接丢弃；
    # 真实但权 <= 0 的边不优于“不匹配”（缺省边权为 0），同样舍弃。
    matching = {}
    total = 0
    for j in range(1, len(V) + 1):
        i = match_y[j]
        if 1 <= i <= len(U):
            u, v = U[i - 1], V[j - 1]
            w = a[i][j]
            if (u, v) in explicit and w > 0:
                matching[u] = v
                total += w

    edge_set = {(u, v) for (u, v) in explicit}
    assert_valid_matching(matching, edge_set, left_nodes, right_nodes)
    return matching, total
