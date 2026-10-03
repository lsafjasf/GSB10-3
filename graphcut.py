"""
graphcut.py — 最小割（最大流）与平衡约束图分割（仅用标准库）。

模型
----
无向图 G=(V,E)，容量均为 1（割大小 = 被切断的边数）。
无向边在流网络里用一对反向、容量均为 1 的弧表示：
对任意割 (S,T)，每条横跨割的无向边恰好有一条正向弧计入容量，
因此最小 s-t 割值 = 分隔 s,t 所需删掉的最少边数。

1) min_s_t_cut:        Dinic 最大流，残量网络上从 s 可达的点集即 S 侧。
2) balanced_min_cut:   在流模型中加入“按侧人数计费”的惩罚边，扫描惩罚
                        参数，从中挑出满足平衡容忍度的最小割（启发式，
                        但精确覆盖 Pareto 包络上的切分点；小图另提供
                        balanced_min_cut_exact 枚举做交叉验证）。
3) assert_cut_valid:   删掉割边后断言两侧不再连通（连通性断言）。
"""

from collections import deque

INF = 10**15
INF_CAP = 10**9  # 强制“锚点”归属时用的大容量


class Dinic:
    """整数容量网络上的 Dinic 最大流（标准库实现）。"""

    def __init__(self, n):
        self.n = n
        self.g = [[] for _ in range(n)]

    def add_edge(self, u, v, cap):
        self.g[u].append([v, cap, len(self.g[v])])
        self.g[v].append([u, 0, len(self.g[u]) - 1])

    def _bfs(self, s, t):
        level = [-1] * self.n
        level[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for v, cap, _ in self.g[u]:
                if cap > 0 and level[v] < 0:
                    level[v] = level[u] + 1
                    q.append(v)
        self._level = level
        return level[t] >= 0

    def _dfs(self, u, t, f, it):
        if u == t:
            return f
        while it[u] < len(self.g[u]):
            e = self.g[u][it[u]]
            v, cap, rev = e
            if cap > 0 and self._level[v] == self._level[u] + 1:
                d = self._dfs(v, t, min(f, cap), it)
                if d:
                    e[1] -= d
                    self.g[v][rev][1] += d
                    return d
            it[u] += 1
        return 0

    def max_flow(self, s, t):
        flow = 0
        while self._bfs(s, t):
            it = [0] * self.n
            while True:
                f = self._dfs(s, t, INF, it)
                if not f:
                    break
                flow += f
        return flow

    def reachable_from(self, s):
        """最大流后在残量网络上 BFS，返回可达点集（割的 S 侧）。"""
        seen = [False] * self.n
        seen[s] = True
        q = deque([s])
        while q:
            u = q.popleft()
            for v, cap, _ in self.g[u]:
                if cap > 0 and not seen[v]:
                    seen[v] = True
                    q.append(v)
        return {v for v in range(self.n) if seen[v]}


# --------------------------------------------------------------------------
# 图工具
# --------------------------------------------------------------------------

def normalize_edges(n, edges):
    """把无向边列表规范成 (u<v) 的集合，自动去重。"""
    s = set()
    for u, v in edges:
        if not (0 <= u < n and 0 <= v < n):
            raise ValueError("边端点越界")
        if u == v:
            raise ValueError("不支持自环")
        s.add((u, v) if u < v else (v, u))
    return s


def edge_list(n, edges):
    return sorted(normalize_edges(n, edges))


def crossing_edges(edges, side):
    """给定 0/1 分组 side，返回横跨割的无向边。"""
    return [(u, v) for (u, v) in edges if side[u] != side[v]]


def reachable_without(n, edges, removed):
    """删掉 removed 中的边后，从 0 号点 BFS 得到的连通分量。"""
    blocked = set()
    for u, v in removed:
        blocked.add((u, v) if u < v else (v, u))
    adj = [[] for _ in range(n)]
    for u, v in edges:
        if (min(u, v), max(u, v)) not in blocked:
            adj[u].append(v)
            adj[v].append(u)
    if n == 0:
        return set()
    seen = {0}
    q = deque([0])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


# --------------------------------------------------------------------------
# 1. 最大流 -> 最小 s-t 割
# --------------------------------------------------------------------------

def min_s_t_cut(n, edges, s, t):
    """
    返回 (cut_size, left, right, cut_edges)。
    最大流结束后，残量网络从 s 可达的点构成 S 侧（最大流最小割定理）。
    """
    if n == 0:
        raise ValueError("空图")
    if s == t:
        raise ValueError("s 与 t 必须不同")
    edges = normalize_edges(n, edges)

    dinic = Dinic(n)
    for u, v in edges:
        # 无向边 = 两条容量 1 的有向弧
        dinic.add_edge(u, v, 1)
        dinic.add_edge(v, u, 1)

    value = dinic.max_flow(s, t)
    left = dinic.reachable_from(s)
    right = set(range(n)) - left

    assert s in left and t in right
    cut_edges = crossing_edges(edges, [0 if i in left else 1 for i in range(n)])
    assert len(cut_edges) == value, "割边数必须等于最大流值"
    return value, left, right, cut_edges


# --------------------------------------------------------------------------
# 2. 连通性断言：删掉割边后两侧确实不再连通
# --------------------------------------------------------------------------

def assert_cut_valid(n, edges, left, right, cut_edges):
    """
    验证割集：
      a) 每条割边都一左一右；
      b) 删除全部割边后，从任一侧内部 BFS 都触达不到另一侧。
    """
    edges = normalize_edges(n, edges)
    left, right = set(left), set(right)
    assert left | right == set(range(n)), "两侧必须覆盖全部节点"
    assert left & right == set(), "两侧必须不相交"

    for u, v in cut_edges:
        assert (u in left) != (v in left), f"割边 {u}-{v} 必须横跨两侧"

    if not left or not right:
        # 退化为单侧空，连通性无从谈起
        assert cut_edges == []
        return

    # 核心断言：删掉割边后，两侧之间不再连通。
    # 注意：最小割的某一侧内部可能本就不连通，因此只验证“跨侧不可达”。
    removed = set(cut_edges)
    comp_left = reachable_component(n, edges, removed, next(iter(left)))
    comp_right = reachable_component(n, edges, removed, next(iter(right)))
    assert comp_left & right == set(), (
        f"删除割边后左侧仍能触达右侧: {sorted(comp_left & right)}"
    )
    assert comp_right & left == set(), (
        f"删除割边后右侧仍能触达左侧: {sorted(comp_right & left)}"
    )


def reachable_component(n, edges, removed, start):
    removed = {(min(u, v), max(u, v)) for u, v in removed}
    adj = [[] for _ in range(n)]
    for u, v in edges:
        if (min(u, v), max(u, v)) not in removed:
            adj[u].append(v)
            adj[v].append(u)
    seen = {start}
    q = deque([start])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


# --------------------------------------------------------------------------
# 3. 平衡约束
# --------------------------------------------------------------------------

def allowed_side_sizes(n, tol):
    """||S| - |T|| <= tol 时 |S| 的合法区间。"""
    lo = (n - tol + 1) // 2          # ceil((n-tol)/2)
    hi = (n + tol) // 2              # floor((n+tol)/2)
    return max(1, lo), min(n - 1, hi)


def _penalized_cut(n, edges, anchor, lam_num, scale, anchor_to_sink):
    """
    在流模型中加入平衡惩罚：
      超源 S* -> 每个原节点 v 加容量 lambda 的弧。
    割值 = 跨割原图边数 + lambda * |T|（落在 T 侧的点都要赔 lambda）。
    lambda 越大，T 侧被压得越小；扫描 lambda 即可在“割边少/两边均衡”
    之间取折衷。anchor 用 INF 弧强制归属，保证割非平凡（两侧都非空）。
    容量整体乘 scale，只做整数运算。

    anchor_to_sink=True : anchor 被强制在 T 侧，惩罚 |T|；
    anchor_to_sink=False: anchor 被强制在 S 侧（对称地惩罚 |S|）。
    """
    src, snk = n, n + 1
    d = Dinic(n + 2)
    for u, v in edges:
        d.add_edge(u, v, scale)
        d.add_edge(v, u, scale)
    pen = lam_num  # lambda * scale
    if anchor_to_sink:
        for v in range(n):
            d.add_edge(src, v, pen)
        d.add_edge(anchor, snk, INF_CAP)
    else:
        for v in range(n):
            d.add_edge(v, snk, pen)
        d.add_edge(src, anchor, INF_CAP)
    value = d.max_flow(src, snk)
    reach = d.reachable_from(src)
    left = {v for v in range(n) if v in reach}
    right = set(range(n)) - left
    return value, left, right


def balanced_min_cut_flow(n, edges, tol):
    """
    基于流模型的平衡割（参数化惩罚法，面向较大图的启发式）：
        要求  ||S| - |T|| <= tol  且  S,T 均非空（n>=2 时）。
    返回 (cut_size, left, right, cut_edges)。

    做法：对每个锚点、对称两个方向扫描惩罚参数 lambda（分母取 n 的
    细网格以覆盖单位容量图上的参数断点），收集满足平衡的割，取割边
    数最小者。

    注意：平衡割是 NP-hard，线性惩罚只能覆盖 (割边数, 侧大小) 平面上
    凸包络上的折衷点，个别实例的最优平衡割可能落在包络之内而漏掉；
    此时返回值是可行割的上界。n <= EXACT_THRESHOLD 时
    balanced_min_cut 会自动改用精确枚举。
    """
    edges = normalize_edges(n, edges)
    if n <= 1:
        return 0, set(range(n)), set(), []

    lo, hi = allowed_side_sizes(n, tol)
    if lo > hi:
        raise ValueError(f"容忍度 {tol} 太小，n={n} 时无法满足")

    scale = 2 * n
    max_deg = max(
        (sum(1 for e in edges if v in e) for v in range(n)), default=0
    )
    # lambda=0 时退化割取整图（割边=0，最不均衡），断点不超过最大度+1
    lambdas = range(0, (max_deg + 2) * scale + 1)

    best = None  # (cut_size, left, right, cut_edges)

    def consider(left, right):
        nonlocal best
        if not left or not right:
            return
        k = len(left)
        if not (lo <= k <= hi):
            return
        side = [0 if i in left else 1 for i in range(n)]
        ce = crossing_edges(edges, side)
        if best is None or len(ce) < best[0]:
            best = (len(ce), set(left), set(right), ce)

    for anchor in range(n):
        for to_sink in (True, False):
            for lam_num in lambdas:
                _, left, right = _penalized_cut(
                    n, edges, anchor, lam_num, scale, to_sink
                )
                consider(left, right)

    if best is None:
        # 参数化扫描未命中可行割（典型于不连通图：凸包络内无可行点），
        # 小图退回精确枚举兜底。
        if n <= EXACT_THRESHOLD:
            return balanced_min_cut_exact(n, edges, tol)
        raise RuntimeError("参数化流方法未找到满足平衡约束的割")
    size, left, right, ce = best
    return size, left, right, ce


EXACT_THRESHOLD = 20  # n 不超过该值时 balanced_min_cut 用精确枚举


def balanced_min_cut_exact(n, edges, tol):
    """
    精确枚举（小图用，n <= ~20）：枚举含节点 0 的一侧子集（位掩码），
    在满足平衡约束的所有割里取跨边数最小。用于交叉验证流方法，
    也是 balanced_min_cut 在小图上的实现。
    """
    edges = normalize_edges(n, edges)
    if n <= 1:
        return 0, set(range(n)), set(), []

    lo, hi = allowed_side_sizes(n, tol)
    if lo > hi:
        raise ValueError(f"容忍度 {tol} 太小，n={n} 时无法满足")

    adj_mask = [0] * n
    for u, v in edges:
        adj_mask[u] |= 1 << v
        adj_mask[v] |= 1 << u

    best_cut = None
    best_s = None
    for sub in range(1 << (n - 1)):      # 节点 0 固定在 S 侧，枚举其余
        s_mask = (sub << 1) | 1
        k = s_mask.bit_count()
        if not (lo <= k <= hi):
            continue
        cut = 0
        m = s_mask
        while m:                          # 跨边数 = sum popcount(adj[v] & ~S)
            lsb = m & -m
            v = lsb.bit_length() - 1
            cut += (adj_mask[v] & ~s_mask).bit_count()
            m ^= lsb
        if best_cut is None or cut < best_cut:
            best_cut, best_s = cut, s_mask

    left = {v for v in range(n) if best_s >> v & 1}
    right = set(range(n)) - left
    side = [0 if i in left else 1 for i in range(n)]
    ce = crossing_edges(edges, side)
    assert len(ce) == best_cut
    return best_cut, left, right, ce


def balanced_min_cut(n, edges, tol):
    """
    带平衡容忍度 tol 的最小割：
        要求  ||S| - |T|| <= tol  且  S,T 均非空（n>=2 时）。
    n <= EXACT_THRESHOLD 时用精确枚举（保证最优）；
    更大的图退化为 balanced_min_cut_flow（流模型上的参数化启发式）。
    """
    if n <= EXACT_THRESHOLD:
        return balanced_min_cut_exact(n, edges, tol)
    return balanced_min_cut_flow(n, edges, tol)


def global_min_cut(n, edges):
    """无平衡约束的全局最小割：枚举 s,t 对求最小 s-t 割（含 0 割）。"""
    edges = normalize_edges(n, edges)
    if n <= 1:
        return 0, set(range(n)), set(), []
    best = None
    for s in range(n):
        for t in range(s + 1, n):
            res = min_s_t_cut(n, edges, s, t)
            if best is None or res[0] < best[0]:
                best = res
            if best[0] == 0:
                return best
    return best
