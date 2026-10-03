"""最近公共祖先（LCA）查询库 —— 倍增法（binary lifting）。

仅依赖 Python 3 标准库。

功能：
- lca(u, v)        最近公共祖先
- distance(u, v)   两点间边数（距离）
- path_max(u, v)   u-v 路径上的最大边权（u == v 时返回 None）
- query(u, v)      一次上溯同时返回 (lca, distance, path_max)
- add_leaf(parent, weight)  动态加入叶子节点（增量维护，均摊 O(log n)）
- rebuild(root)    结构发生非叶子类变更后整体重建索引

复杂度（n 为节点数）：
- 预处理：时间 O(n log n)，空间 O(n log n)
- 单次查询：O(log n)，低于线性
- add_leaf：均摊 O(log n)（表层数倍增扩容，摊还成本）
- 朴素参考实现（reference.py）：每次查询 O(h)，用于对拍

边权约定：允许任意可比较的数值（含负数与浮点数）；
path_max 在路径无边（u == v）时返回 None。
"""

from collections import deque

__all__ = ["LCA"]


def _amax(*vals):
    """忽略 None 的最大值；全部为 None 时返回 None。"""
    best = None
    for val in vals:
        if val is not None and (best is None or val > best):
            best = val
    return best


class LCA:
    """静态树 + 增量叶子插入的 LCA 索引（倍增法）。

    节点用 0 .. n-1 的整数编号，边带权。
    """

    def __init__(self, n, edges, root=0):
        """n: 节点数；edges: [(u, v, w), ...] 无向带权边，须构成一棵树；root: 根。"""
        if n < 1:
            raise ValueError("n 必须 >= 1")
        if not 0 <= root < n:
            raise ValueError("root 超出范围")
        if len(edges) != n - 1:
            raise ValueError("树的边数必须恰好为 n - 1")
        self._adj = [[] for _ in range(n)]
        for u, v, w in edges:
            self._check_node(u)
            self._check_node(v)
            self._adj[u].append((v, w))
            self._adj[v].append((u, w))
        # 预留倍增表层数，减少 add_leaf 时的扩容次数
        self._log = max(1, (n - 1).bit_length())
        self._build(root)

    # ------------------------------------------------------------------ #
    # 查询接口（均为 O(log n)）
    # ------------------------------------------------------------------ #

    def lca(self, u, v):
        """u 与 v 的最近公共祖先。"""
        self._check_node(u)
        self._check_node(v)
        if self._depth[u] < self._depth[v]:
            u, v = v, u
        u = self._lift(u, self._depth[u] - self._depth[v])
        if u == v:
            return u
        for k in range(self._log - 1, -1, -1):
            if self._up[k][u] != self._up[k][v]:
                u = self._up[k][u]
                v = self._up[k][v]
        return self._up[0][u]

    def distance(self, u, v):
        """u 与 v 之间的边数。"""
        ancestor = self.lca(u, v)
        return self._depth[u] + self._depth[v] - 2 * self._depth[ancestor]

    def path_max(self, u, v):
        """u-v 路径上的最大边权；u == v（路径无边）时返回 None。"""
        self._check_node(u)
        self._check_node(v)
        if self._depth[u] < self._depth[v]:
            u, v = v, u
        best = None
        diff = self._depth[u] - self._depth[v]
        k = 0
        while diff:
            if diff & 1:
                best = _amax(best, self._mx[k][u])
                u = self._up[k][u]
            diff >>= 1
            k += 1
        if u == v:
            return best
        for k in range(self._log - 1, -1, -1):
            if self._up[k][u] != self._up[k][v]:
                best = _amax(best, self._mx[k][u], self._mx[k][v])
                u = self._up[k][u]
                v = self._up[k][v]
        return _amax(best, self._mx[0][u], self._mx[0][v])

    def query(self, u, v):
        """一次上溯同时返回 (lca, distance, path_max)，比分开调用省一趟。"""
        self._check_node(u)
        self._check_node(v)
        du, dv = self._depth[u], self._depth[v]
        best = None
        if self._depth[u] < self._depth[v]:
            u, v = v, u
        diff = self._depth[u] - self._depth[v]
        steps, k = diff, 0
        while steps:
            if steps & 1:
                best = _amax(best, self._mx[k][u])
                u = self._up[k][u]
            steps >>= 1
            k += 1
        if u == v:
            return u, diff, best
        for k in range(self._log - 1, -1, -1):
            if self._up[k][u] != self._up[k][v]:
                best = _amax(best, self._mx[k][u], self._mx[k][v])
                u = self._up[k][u]
                v = self._up[k][v]
        best = _amax(best, self._mx[0][u], self._mx[0][v])
        ancestor = self._up[0][u]
        return ancestor, du + dv - 2 * self._depth[ancestor], best

    def depth(self, u):
        """节点深度（根为 0）。"""
        self._check_node(u)
        return self._depth[u]

    # ------------------------------------------------------------------ #
    # 动态变更
    # ------------------------------------------------------------------ #

    def add_leaf(self, parent, weight=0):
        """在 parent 下新增一个叶子节点，边权为 weight，返回新节点编号。

        增量维护倍增表，均摊 O(log n)，无需重建索引。
        仅支持“挂叶子”这一类变更；其余结构变更请用 rebuild()。
        """
        self._check_node(parent)
        v = len(self._adj)
        self._adj.append([(parent, weight)])
        self._adj[parent].append((v, weight))
        self._depth.append(self._depth[parent] + 1)

        # 深度超过当前表层数时，先为已有节点整表补层
        needed = self._depth[v].bit_length()
        while self._log < needed:
            k = self._log
            prev_up, prev_mx = self._up[k - 1], self._mx[k - 1]
            new_up, new_mx = [0] * v, [None] * v
            for u in range(v):
                mid = prev_up[u]
                new_up[u] = prev_up[mid]
                new_mx[u] = _amax(prev_mx[u], prev_mx[mid])
            self._up.append(new_up)
            self._mx.append(new_mx)
            self._log += 1

        # 为新节点补各层条目
        for k in range(self._log):
            self._up[k].append(0)
            self._mx[k].append(None)
        self._up[0][v] = parent
        self._mx[0][v] = weight
        for k in range(1, self._log):
            mid = self._up[k - 1][v]
            self._up[k][v] = self._up[k - 1][mid]
            self._mx[k][v] = _amax(self._mx[k - 1][v], self._mx[k - 1][mid])
        return v

    def rebuild(self, root=0):
        """以 root 为根整体重建索引，O(n log n)。

        适用场景：发生了 add_leaf 无法表达的结构性变更
        （如换根、子树搬移、删点、边权批量修改）。
        """
        self._check_node(root)
        self._log = max(1, (len(self._adj) - 1).bit_length())
        self._build(root)

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #

    def _build(self, root):
        n = len(self._adj)
        self._depth = [0] * n
        self._up = [[0] * n for _ in range(self._log)]
        self._mx = [[None] * n for _ in range(self._log)]
        visited = bytearray(n)
        visited[root] = 1
        queue = deque([root])
        while queue:
            u = queue.popleft()
            for v, w in self._adj[u]:
                if visited[v]:
                    continue
                visited[v] = 1
                self._depth[v] = self._depth[u] + 1
                self._up[0][v] = u
                self._mx[0][v] = w
                queue.append(v)
        if not all(visited):
            raise ValueError("边集不构成一棵连通树")
        for k in range(1, self._log):
            prev_up, prev_mx = self._up[k - 1], self._mx[k - 1]
            cur_up, cur_mx = self._up[k], self._mx[k]
            for v in range(n):
                mid = prev_up[v]
                cur_up[v] = prev_up[mid]
                cur_mx[v] = _amax(prev_mx[v], prev_mx[mid])

    def _lift(self, u, steps):
        """将 u 向上移动 steps 步。"""
        k = 0
        while steps:
            if steps & 1:
                u = self._up[k][u]
            steps >>= 1
            k += 1
        return u

    def _check_node(self, u):
        if not 0 <= u < len(self._adj):
            raise ValueError(f"节点 {u} 不存在（当前共 {len(self._adj)} 个节点）")

    def __len__(self):
        return len(self._adj)

    def __contains__(self, u):
        return 0 <= u < len(self._adj)
