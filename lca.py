"""最近公共祖先（LCA）查询库。

算法：倍增（binary lifting）。
- 预处理：O(N log N) 时间、O(N log N) 空间
- 单次查询（LCA / 距离 / 路径最大边权）：O(log N)

同时提供朴素逐层上溯参考实现 NaiveTree，用于对拍。

只依赖 Python 3 标准库。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Dict, Hashable, Iterable, List, Optional, Tuple

_NO_EDGE = float("-inf")  # 路径上没有边（两节点相同）时的最大值约定


class Tree:
    """带权有根树，节点标签可为任意可哈希对象。

    用法：
        t = Tree()
        t.add_node("root")
        t.add_node("a", parent="root", weight=3)
        t.build_index()
        t.lca("a", "root")
        t.distance("a", "root")      # 1
        t.path_max_weight("a", "root")  # 3
    """

    def __init__(self) -> None:
        # 增量建树阶段使用的结构（按标签存储）
        self._parent: Dict[Hashable, Hashable] = {}
        self._edge_weight: Dict[Hashable, float] = {}
        self._children: Dict[Hashable, List[Hashable]] = {}
        self._root: Optional[Hashable] = None
        self._insert_order: List[Hashable] = []

        # 索引状态
        self._indexed = False
        self._pending: List[Hashable] = []  # build 之后新增、尚未进索引的节点

        self._id: Dict[Hashable, int] = {}
        self._label: List[Hashable] = []
        self._depth: List[int] = []
        self._up: List[List[int]] = []        # _up[k][x]：x 向上 2^k 步的节点 id
        self._mx: List[List[float]] = []      # _mx[k][x]：这段路上的最大边权
        self._LOG = 0

    # ------------------------------------------------------------------ #
    # 建树
    # -------------------------------------------------------------------- #
    def add_node(
        self,
        node: Hashable,
        parent: Optional[Hashable] = None,
        weight: float = 0,
    ) -> None:
        """加入一个节点。

        - 第一个加入的节点为根（parent 必须为 None）。
        - 其余节点必须挂在一个已存在的节点下，weight 为该父子边的权重。
        - 新节点只能作为叶子加入（节点加入后不支持再改父节点/边权，
          这类“结构性修改”需要重新 build_index，见模块说明）。
        每次 add_node 都会使查询索引失效：批量加完后调用 build_index()
        全量重建，或调用 extend_index() 做增量追加。
        """
        if node in self._parent:
            raise ValueError(f"节点 {node!r} 已存在")
        if parent is None:
            if self._root is not None:
                raise ValueError(
                    f"根已经是 {self._root!r}；不支持森林，新节点必须指定父节点"
                )
            self._root = node
        else:
            if parent not in self._parent:
                raise ValueError(f"父节点 {parent!r} 不存在")
            weight = float(weight)
        self._parent[node] = parent if parent is not None else node
        self._edge_weight[node] = weight if parent is not None else _NO_EDGE
        self._children.setdefault(parent, []).append(node)
        self._insert_order.append(node)
        self._pending.append(node)
        self._indexed = False

    @property
    def node_count(self) -> int:
        return len(self._insert_order)

    @property
    def indexed(self) -> bool:
        return self._indexed

    # ------------------------------------------------------------------ #
    # 索引构建 / 增量更新
    # ------------------------------------------------------------------ #
    def build_index(self) -> "Tree":
        """全量构建倍增索引，复杂度 O(N log N)。

        以下情况必须（重新）调用本方法：
        1. 首次建完树之后；
        2. 修改了已有边的父节点或权重（结构性修改）；
        3. 在已有节点之间插入节点、合并两棵树/新增根；
        4. 树高增长超过当前 LOG 容量（extend_index 会自动转全量重建）；
        5. 一次性追加大量节点，且不想付出逐条维护的常数开销时。
        """
        n = self.node_count
        if n == 0:
            raise ValueError("空树无法建立索引")

        self._label = list(self._insert_order)
        self._id = {label: i for i, label in enumerate(self._label)}

        max_depth = 0
        self._depth = [0] * n
        par0 = [0] * n
        mx0 = [_NO_EDGE] * n
        for label in self._label:  # 插入顺序保证父先于子
            x = self._id[label]
            p = self._parent[label]
            if p == label:
                par0[x] = x
            else:
                pid = self._id[p]
                par0[x] = pid
                mx0[x] = self._edge_weight[label]
                self._depth[x] = self._depth[pid] + 1
                if self._depth[x] > max_depth:
                    max_depth = self._depth[x]

        # 覆盖整条最深链：2^(LOG-1) >= max_depth 即可，多取 1 行更稳妥
        self._LOG = max(1, int(math.log2(max_depth + 1)) + 2) if max_depth else 1
        self._up = [par0]
        self._mx = [mx0]
        for k in range(1, self._LOG):
            prev_up = self._up[k - 1]
            prev_mx = self._mx[k - 1]
            cur_up = [0] * n
            cur_mx = [_NO_EDGE] * n
            for x in range(n):
                mid = prev_up[x]
                cur_up[x] = prev_up[mid]
                a = prev_mx[x]
                b = prev_mx[mid]
                cur_mx[x] = a if a >= b else b
            self._up.append(cur_up)
            self._mx.append(cur_mx)

        self._pending = []
        self._indexed = True
        return self

    def extend_index(self) -> "Tree":
        """增量追加自上次 build/extend 之后加入的叶子节点。

        仅支持“新节点作为叶子挂到已索引（或本次更早追加）的节点下”。
        每个新节点 O(log N)；若新树高超过当前 LOG 容量，自动转为
        O(N log N) 的全量重建。
        """
        if not self._pending:
            return self
        if not self._up:  # 从未 build 过
            return self.build_index()

        # 任一待加入节点会让树高超出表的覆盖范围 => 全量重建
        for label in self._pending:
            p = self._parent[label]
            d = self._depth[self._id[p]] + 1 if p != label else 0
            if (1 << (self._LOG - 1)) <= d:
                return self.build_index()

        for label in self._pending:  # 插入顺序保证父先处理
            p = self._parent[label]
            x = len(self._label)
            self._id[label] = x
            self._label.append(label)
            pid = self._id[p]
            self._depth.append(self._depth[pid] + 1)
            self._up[0].append(pid)
            self._mx[0].append(self._edge_weight[label])
            for k in range(1, self._LOG):
                mid = self._up[k - 1][x]
                self._up[k].append(self._up[k - 1][mid])
                a = self._mx[k - 1][x]
                b = self._mx[k - 1][mid]
                self._mx[k].append(a if a >= b else b)

        self._pending = []
        self._indexed = True
        return self

    def _require_index(self, u: Hashable, v: Hashable) -> Tuple[int, int]:
        if not self._indexed:
            raise RuntimeError(
                "索引已失效：结构变化后请调用 build_index()（全量重建）"
                "或 extend_index()（仅追加叶子时可增量）"
            )
        if u not in self._id or v not in self._id:
            raise KeyError("查询的节点不在已索引的树中")
        return self._id[u], self._id[v]

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def lca(self, u: Hashable, v: Hashable) -> Hashable:
        """最近公共祖先，O(log N)。节点自身是自身的祖先。"""
        a, b = self._require_index(u, v)
        return self._label[self._lca_ids(a, b)]

    def _lca_ids(self, a: int, b: int) -> int:
        if self._depth[a] < self._depth[b]:
            a, b = b, a
        a = self._lift_to_depth(a, self._depth[b])
        if a == b:
            return a
        up = self._up
        for k in range(self._LOG - 1, -1, -1):
            ua, ub = up[k][a], up[k][b]
            if ua != ub:
                a, b = ua, ub
        return up[0][a]

    def _lift_to_depth(self, x: int, target: int) -> int:
        diff = self._depth[x] - target
        k = 0
        while diff:
            if diff & 1:
                x = self._up[k][x]
            diff >>= 1
            k += 1
        return x

    def distance(self, u: Hashable, v: Hashable) -> int:
        """两节点间的边数（最短且唯一路径），O(log N)。"""
        a, b = self._require_index(u, v)
        w = self._lca_ids(a, b)
        return self._depth[a] + self._depth[b] - 2 * self._depth[w]

    def path_max_weight(self, u: Hashable, v: Hashable) -> float:
        """u-v 路径上的最大边权，O(log N)。

        u == v 时路径上没有边，按约定返回 float('-inf')。
        """
        a, b = self._require_index(u, v)
        return self._path_max_ids(a, b)

    def _path_max_ids(self, a: int, b: int) -> float:
        result = _NO_EDGE
        if self._depth[a] < self._depth[b]:
            a, b = b, a
        # a 抬到与 b 同深，沿途统计
        diff = self._depth[a] - self._depth[b]
        k = 0
        while diff:
            if diff & 1:
                w = self._mx[k][a]
                if w > result:
                    result = w
                a = self._up[k][a]
            diff >>= 1
            k += 1
        if a == b:
            return result
        for k in range(self._LOG - 1, -1, -1):
            ua, ub = self._up[k][a], self._up[k][b]
            if ua != ub:
                wa = self._mx[k][a]
                wb = self._mx[k][b]
                if wa > result:
                    result = wa
                if wb > result:
                    result = wb
                a, b = ua, ub
        wa = self._mx[0][a]
        wb = self._mx[0][b]
        if wa > result:
            result = wa
        if wb > result:
            result = wb
        return result

    def query(self, u: Hashable, v: Hashable) -> Tuple[Hashable, int, float]:
        """一次返回 (LCA, 距离, 路径最大边权)，只做一轮倍增。"""
        a, b = self._require_index(u, v)
        w = self._lca_ids(a, b)
        dist = self._depth[a] + self._depth[b] - 2 * self._depth[w]
        mx = self._path_max_ids(a, b)
        return self._label[w], dist, mx


class NaiveTree:
    """逐层上溯的参考实现，O(N)/次查询。仅用于对拍。"""

    def __init__(self) -> None:
        self.parent: Dict[Hashable, Hashable] = {}
        self.weight: Dict[Hashable, float] = {}
        self.root: Optional[Hashable] = None

    def add_node(
        self, node: Hashable, parent: Optional[Hashable] = None, weight: float = 0
    ) -> None:
        if parent is None:
            if self.root is not None:
                raise ValueError("根已存在")
            self.root = node
            self.parent[node] = node
            self.weight[node] = _NO_EDGE
        else:
            self.parent[node] = parent
            self.weight[node] = float(weight)

    def _depth(self, x: Hashable) -> int:
        d = 0
        while self.parent[x] != x:
            x = self.parent[x]
            d += 1
        return d

    def query(
        self, u: Hashable, v: Hashable
    ) -> Tuple[Hashable, int, float]:
        du, dv = self._depth(u), self._depth(v)
        dist = 0
        mx = _NO_EDGE
        while du > dv:
            w = self.weight[u]
            if w > mx:
                mx = w
            u = self.parent[u]
            du -= 1
            dist += 1
        while dv > du:
            w = self.weight[v]
            if w > mx:
                mx = w
            v = self.parent[v]
            dv -= 1
            dist += 1
        while u != v:
            wu, wv = self.weight[u], self.weight[v]
            if wu > mx:
                mx = wu
            if wv > mx:
                mx = wv
            u = self.parent[u]
            v = self.parent[v]
            dist += 2
        return u, dist, mx


def build_from_edges(
    edges: Iterable[Tuple[Hashable, Hashable, float]],
    root: Hashable,
) -> Tuple[Tree, NaiveTree]:
    """从 (父, 子, 权重) 边列表同时构造倍增树与朴素树（测试/示例用）。"""
    t, ref = Tree(), NaiveTree()
    t.add_node(root)
    ref.add_node(root)
    adj: Dict[Hashable, List[Tuple[Hashable, float]]] = {}
    nodes = {root}
    for p, c, w in edges:
        adj.setdefault(p, []).append((c, w))
        nodes.add(c)
    q = deque([root])
    while q:
        p = q.popleft()
        for c, w in adj.get(p, []):
            t.add_node(c, parent=p, weight=w)
            ref.add_node(c, parent=p, weight=w)
            q.append(c)
    return t, ref
