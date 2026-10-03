"""可持久化线段树（Persistent Segment Tree）。

数组下标区间为半开区间 [0, n)。初始时所有位置均为零值。

核心性质：
- 每次单点更新只克隆从根到被修改叶子的路径，路径之外的节点
  在新旧版本之间共享，因此节点数每次更新仅增加 O(log n)。
- 每个版本只是一个根节点编号，根一旦生成便永不改变，
  所以在旧版本上的查询结果不受后续版本影响。

为减少 Python 对象开销，所有节点以并行数组（结构数组）方式存储：
    left[i] / right[i] : 左右子节点编号，0 表示空子树
    sums[i]            : 区间和
    mins[i] / maxs[i]  : 区间最小值 / 最大值

编号 0 保留为空节点（NULL）。
"""

import math

INF = math.inf

# 空区间上的聚合单位元：sum 为 0，min 为 +inf，max 为 -inf。
EMPTY_RESULT = (0, INF, -INF)


class PersistentRangeTree:
    def __init__(self, size):
        if not isinstance(size, int) or isinstance(size, bool):
            raise TypeError("size 必须是整数")
        if size < 0:
            raise ValueError("size 不能为负数")
        self._n = size
        # 下标 0 为空节点；初值为全零意味着空节点的聚合与零值子树一致。
        self._left = [0]
        self._right = [0]
        self._sums = [0]
        self._mins = [0]
        self._maxs = [0]
        # 版本 0：空树（全部位置为零值）。
        self._roots = [0]

    @property
    def size(self):
        """下标域大小（合法下标为 0 .. size-1）。"""
        return self._n

    @property
    def node_count(self):
        """当前结构数组中已分配的物理节点总数（含空节点 0 号）。"""
        return len(self._left)

    @property
    def version_count(self):
        """已有版本数（版本 0 为初始零值版本）。"""
        return len(self._roots)

    def roots(self):
        """返回所有版本根编号的快照副本。"""
        return list(self._roots)

    def _alloc(self, left, right, sums, mins, maxs):
        idx = len(self._left)
        self._left.append(left)
        self._right.append(right)
        self._sums.append(sums)
        self._mins.append(mins)
        self._maxs.append(maxs)
        return idx

    def update(self, version, pos, value):
        """基于 version 版本，把 pos 位置设为 value，返回新版本号。

        旧版本保持不变。value 需支持 +、min、max，通常为 int 或 float。
        """
        if not isinstance(version, int) or isinstance(version, bool):
            raise TypeError("version 必须是整数")
        if not (0 <= version < len(self._roots)):
            raise IndexError(f"版本 {version} 不存在，合法范围 [0, {len(self._roots) - 1}]")
        if not isinstance(pos, int) or isinstance(pos, bool):
            raise TypeError("pos 必须是整数")
        if not (0 <= pos < self._n):
            raise IndexError(f"pos {pos} 越界，合法范围 [0, {self._n - 1}]")

        new_root = self._update(self._roots[version], 0, self._n, pos, value)
        self._roots.append(new_root)
        return len(self._roots) - 1

    def _update(self, old, lo, hi, pos, value):
        # 克隆当前节点；叶子直接采用新值，否则递归后重建聚合。
        node = self._alloc(self._left[old], self._right[old],
                           self._sums[old], self._mins[old], self._maxs[old])
        if hi - lo == 1:
            self._sums[node] = value
            self._mins[node] = value
            self._maxs[node] = value
            return node

        mid = (lo + hi) // 2
        if pos < mid:
            child = self._update(self._left[old], lo, mid, pos, value)
            self._left[node] = child
        else:
            child = self._update(self._right[old], mid, hi, pos, value)
            self._right[node] = child
        self._pull(node)
        return node

    def _pull(self, node):
        lc = self._left[node]
        rc = self._right[node]
        self._sums[node] = self._sums[lc] + self._sums[rc]
        self._mins[node] = min(self._mins[lc], self._mins[rc])
        self._maxs[node] = max(self._maxs[lc], self._maxs[rc])

    def query(self, version, left, right):
        """查询版本 version 上半开区间 [left, right) 的 (sum, min, max)。

        空区间（left >= right）返回单位元 (0, +inf, -inf)。
        """
        if not isinstance(version, int) or isinstance(version, bool):
            raise TypeError("version 必须是整数")
        if not (0 <= version < len(self._roots)):
            raise IndexError(f"版本 {version} 不存在，合法范围 [0, {len(self._roots) - 1}]")
        if not (0 <= left <= self._n) or not (0 <= right <= self._n):
            raise IndexError(f"区间端点越界，合法范围 [0, {self._n}]")
        if left >= right:
            return EMPTY_RESULT
        return self._query(self._roots[version], 0, self._n, left, right)

    def _query(self, node, lo, hi, ql, qr):
        if ql <= lo and hi <= qr:
            return (self._sums[node], self._mins[node], self._maxs[node])
        mid = (lo + hi) // 2
        result = EMPTY_RESULT
        if ql < mid:
            result = self._merge(
                result, self._query(self._left[node], lo, mid, ql, qr))
        if qr > mid:
            result = self._merge(
                result, self._query(self._right[node], mid, hi, ql, qr))
        return result

    @staticmethod
    def _merge(a, b):
        return (a[0] + b[0], min(a[1], b[1]), max(a[2], b[2]))
