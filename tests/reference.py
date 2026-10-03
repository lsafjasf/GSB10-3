"""朴素参考实现：逐层上溯，用于与倍增法对拍。

不建任何索引，每次查询 O(h)（h 为树高），实现简单直白、易于确认正确性。
接口与 lca.LCA 对齐：lca / distance / path_max / add_leaf。
"""

from collections import deque


class NaiveLCA:
    def __init__(self, n, edges, root=0):
        self._parent = [(-1, None)] * n  # (父节点, 到父节点的边权)
        self._depth = [0] * n
        adj = [[] for _ in range(n)]
        for u, v, w in edges:
            adj[u].append((v, w))
            adj[v].append((u, w))
        visited = bytearray(n)
        visited[root] = 1
        queue = deque([root])
        while queue:
            u = queue.popleft()
            for v, w in adj[u]:
                if visited[v]:
                    continue
                visited[v] = 1
                self._parent[v] = (u, w)
                self._depth[v] = self._depth[u] + 1
                queue.append(v)

    def lca(self, u, v):
        u, v = self._align(u, v)
        while u != v:
            u = self._parent[u][0]
            v = self._parent[v][0]
        return u

    def distance(self, u, v):
        ancestor = self.lca(u, v)
        return self._depth[u] + self._depth[v] - 2 * self._depth[ancestor]

    def path_max(self, u, v):
        u, v, best = self._align_track(u, v)
        while u != v:
            best = _max(best, self._parent[u][1], self._parent[v][1])
            u = self._parent[u][0]
            v = self._parent[v][0]
        return best

    def add_leaf(self, parent, weight=0):
        v = len(self._parent)
        self._parent.append((parent, weight))
        self._depth.append(self._depth[parent] + 1)
        return v

    def _align(self, u, v):
        """把较深一端抬到同一深度，返回 (u, v)。"""
        while self._depth[u] > self._depth[v]:
            u = self._parent[u][0]
        while self._depth[v] > self._depth[u]:
            v = self._parent[v][0]
        return u, v

    def _align_track(self, u, v):
        """抬深端并记录途经最大边权，返回 (u, v, best)。"""
        best = None
        while self._depth[u] > self._depth[v]:
            best = _max(best, self._parent[u][1])
            u = self._parent[u][0]
        while self._depth[v] > self._depth[u]:
            best = _max(best, self._parent[v][1])
            v = self._parent[v][0]
        return u, v, best


def _max(*vals):
    best = None
    for val in vals:
        if val is not None and (best is None or val > best):
            best = val
    return best
