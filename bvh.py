"""三角形网格的 BVH 空间索引（仅标准库）。

- build:            自顶向下，按质心最长轴中位切分
- query_aabb:       与查询盒重叠的三角形（叶子处做 SAT 精确判定）
- query_ray:        命中三角形的 (t, 索引) 列表，按 t 升序
- query_ray_nearest:最近命中，带分支限界
- update:           增量更新（摘除旧叶子 + 按 SAH 代价重新插入 + 自底向上 refit）

所有几何判定与 brute.py 共用 geometry.py 中的函数，保证对拍口径一致。
"""
import math

from geometry import (
    boxes_overlap,
    ray_box,
    ray_triangle,
    surface_area,
    tri_bounds,
    tri_box_overlap,
    union_bounds,
)

_INF = math.inf


class _Node:
    __slots__ = ("bmin", "bmax", "left", "right", "tris", "parent")

    def __init__(self):
        self.bmin = None
        self.bmax = None
        self.left = None
        self.right = None
        self.tris = None  # 叶子：三角形索引列表；内部节点：None
        self.parent = None

    @property
    def is_leaf(self):
        return self.tris is not None


def _make_leaf(tri_ids, triangles, bounds_cache=None):
    node = _Node()
    node.tris = list(tri_ids)
    if bounds_cache is not None:
        bmin, bmax = bounds_cache[node.tris[0]]
        for i in node.tris[1:]:
            lo, hi = bounds_cache[i]
            bmin, bmax = union_bounds(bmin, bmax, lo, hi)
    else:
        bmin, bmax = tri_bounds(triangles[node.tris[0]])
        for i in node.tris[1:]:
            lo, hi = tri_bounds(triangles[i])
            bmin, bmax = union_bounds(bmin, bmax, lo, hi)
    node.bmin, node.bmax = bmin, bmax
    return node


class BVH:
    def __init__(self, triangles=None, leaf_size=4):
        self.triangles = []
        self.root = None
        self.leaf_of = {}  # 三角形索引 -> 叶子节点
        self.leaf_size = max(1, leaf_size)
        if triangles:
            self.build(triangles)

    # ------------------------------------------------------------------ 建树
    def build(self, triangles):
        self.triangles = [tuple(tuple(float(c) for c in v) for v in tri) for tri in triangles]
        self.leaf_of = {}
        self._tri_bounds = [tri_bounds(t) for t in self.triangles]
        self._tri_centroid = [
            ((t[0][0] + t[1][0] + t[2][0]) / 3.0,
             (t[0][1] + t[1][1] + t[2][1]) / 3.0,
             (t[0][2] + t[1][2] + t[2][2]) / 3.0)
            for t in self.triangles
        ]
        ids = list(range(len(self.triangles)))
        self.root = self._build(ids) if ids else None
        self._tri_bounds = None
        self._tri_centroid = None

    def _build(self, ids):
        if len(ids) <= self.leaf_size:
            leaf = _make_leaf(ids, self.triangles, self._tri_bounds)
            for i in ids:
                self.leaf_of[i] = leaf
            return leaf

        split = self._sah_split(ids)
        if split is None:  # 质心全相同等极端情形：均分兜底
            mid = len(ids) // 2
            left_ids, right_ids = ids[:mid], ids[mid:]
        else:
            left_ids, right_ids = split

        node = _Node()
        node.left = self._build(left_ids)
        node.right = self._build(right_ids)
        node.left.parent = node
        node.right.parent = node
        node.bmin, node.bmax = union_bounds(
            node.left.bmin, node.left.bmax, node.right.bmin, node.right.bmax
        )
        return node

    _SAH_BINS = 12

    def _sah_split(self, ids):
        """分桶 SAH：在三个轴上找表面积启发代价最小的切分。

        返回 (left_ids, right_ids)；无法有效切分时返回 None。
        """
        cents = [self._tri_centroid[i] for i in ids]
        cmin = [_INF, _INF, _INF]
        cmax = [-_INF, -_INF, -_INF]
        for c in cents:
            for a in range(3):
                if c[a] < cmin[a]:
                    cmin[a] = c[a]
                if c[a] > cmax[a]:
                    cmax[a] = c[a]

        best_cost = _INF
        best_axis = -1
        best_bin = -1
        bins = self._SAH_BINS
        for axis in range(3):
            lo = cmin[axis]
            hi = cmax[axis]
            if hi - lo < 1e-12:
                continue
            scale = bins / (hi - lo)
            counts = [0] * bins
            bmins = [None] * bins
            bmaxs = [None] * bins
            for i, c in zip(ids, cents):
                b = int((c[axis] - lo) * scale)
                if b >= bins:
                    b = bins - 1
                counts[b] += 1
                tmin, tmax = self._tri_bounds[i]
                if bmins[b] is None:
                    bmins[b], bmaxs[b] = tmin, tmax
                else:
                    bmins[b], bmaxs[b] = union_bounds(bmins[b], bmaxs[b], tmin, tmax)
            # 前缀/后缀扫描求最优切分
            left_count = 0
            left_min = left_max = None
            left_areas = [0.0] * (bins - 1)
            left_counts = [0] * (bins - 1)
            for b in range(bins - 1):
                if counts[b]:
                    left_count += counts[b]
                    if left_min is None:
                        left_min, left_max = bmins[b], bmaxs[b]
                    else:
                        left_min, left_max = union_bounds(
                            left_min, left_max, bmins[b], bmaxs[b])
                left_counts[b] = left_count
                left_areas[b] = surface_area(left_min, left_max) if left_min else 0.0
            right_count = 0
            right_min = right_max = None
            for b in range(bins - 1, 0, -1):
                if counts[b]:
                    right_count += counts[b]
                    if right_min is None:
                        right_min, right_max = bmins[b], bmaxs[b]
                    else:
                        right_min, right_max = union_bounds(
                            right_min, right_max, bmins[b], bmaxs[b])
                lc = left_counts[b - 1]
                if lc == 0 or right_count == 0:
                    continue
                cost = (lc * left_areas[b - 1]
                        + right_count * surface_area(right_min, right_max))
                if cost < best_cost:
                    best_cost = cost
                    best_axis = axis
                    best_bin = b

        if best_axis < 0:
            return None
        lo = cmin[best_axis]
        hi = cmax[best_axis]
        scale = bins / (hi - lo)
        left_ids = []
        right_ids = []
        for i, c in zip(ids, cents):
            b = int((c[best_axis] - lo) * scale)
            if b >= bins:
                b = bins - 1
            (left_ids if b < best_bin else right_ids).append(i)
        if not left_ids or not right_ids:
            return None
        return left_ids, right_ids

    # ------------------------------------------------------------------ 查询
    def query_aabb(self, qmin, qmax):
        """返回与查询盒精确相交的三角形索引列表（升序）。"""
        out = []
        if self.root is None:
            return out
        stack = [self.root]
        push = stack.append
        pop = stack.pop
        while stack:
            node = pop()
            if not boxes_overlap(node.bmin, node.bmax, qmin, qmax):
                continue
            if node.is_leaf:
                for i in node.tris:
                    if tri_box_overlap(self.triangles[i], qmin, qmax):
                        out.append(i)
            else:
                push(node.left)
                push(node.right)
        out.sort()
        return out

    def query_ray(self, origin, direction):
        """返回 [(t, 三角形索引), ...]，按 t 升序。"""
        hits = []
        if self.root is None:
            return hits
        stack = [self.root]
        push = stack.append
        pop = stack.pop
        while stack:
            node = pop()
            if ray_box(origin, direction, node.bmin, node.bmax) is None:
                continue
            if node.is_leaf:
                for i in node.tris:
                    v0, v1, v2 = self.triangles[i]
                    t = ray_triangle(origin, direction, v0, v1, v2)
                    if t is not None:
                        hits.append((t, i))
            else:
                push(node.left)
                push(node.right)
        hits.sort(key=lambda h: (h[0], h[1]))
        return hits

    def query_ray_nearest(self, origin, direction):
        """返回 (t, 三角形索引) 或 None；t 相同取索引较小者。"""
        if self.root is None:
            return None
        best_t = _INF
        best_i = -1
        stack = [self.root]
        push = stack.append
        pop = stack.pop
        while stack:
            node = pop()
            if ray_box(origin, direction, node.bmin, node.bmax, best_t) is None:
                continue
            if node.is_leaf:
                for i in node.tris:
                    v0, v1, v2 = self.triangles[i]
                    t = ray_triangle(origin, direction, v0, v1, v2)
                    if t is not None and (t < best_t or (t == best_t and i < best_i)):
                        best_t = t
                        best_i = i
            else:
                push(node.left)
                push(node.right)
        if best_i < 0:
            return None
        return (best_t, best_i)

    # -------------------------------------------------------------- 增量更新
    def update(self, updates):
        """批量增量更新。updates: {三角形索引: 新三角形} 或 [(索引, 新三角形), ...]"""
        if hasattr(updates, "items"):
            items = list(updates.items())
        else:
            items = list(updates)
        for idx, tri in items:
            self.triangles[idx] = tuple(tuple(float(c) for c in v) for v in tri)
        # 先全部摘除，再全部插回，避免互相干扰
        for idx, _ in items:
            self._remove_leaf(idx)
        for idx, _ in items:
            self._insert_leaf(idx)

    def _remove_leaf(self, idx):
        leaf = self.leaf_of.pop(idx)
        leaf.tris.remove(idx)
        if leaf.tris:
            # 叶子内还有其他三角形：只需 refit
            self._refit_leaf(leaf)
            return
        parent = leaf.parent
        if parent is None:
            self.root = None
            return
        sibling = parent.left if parent.right is leaf else parent.right
        grand = parent.parent
        sibling.parent = grand
        if grand is None:
            self.root = sibling
        else:
            if grand.left is parent:
                grand.left = sibling
            else:
                grand.right = sibling
            self._refit_up(grand)

    def _insert_leaf(self, idx):
        leaf = _make_leaf([idx], self.triangles)
        self.leaf_of[idx] = leaf
        if self.root is None:
            self.root = leaf
            return
        # 自根向下，沿路扩张包围盒，按 SAH 代价选下降方向
        node = self.root
        while not node.is_leaf:
            node.bmin, node.bmax = union_bounds(node.bmin, node.bmax, leaf.bmin, leaf.bmax)
            cost_left = surface_area(*union_bounds(
                node.left.bmin, node.left.bmax, leaf.bmin, leaf.bmax))
            cost_right = surface_area(*union_bounds(
                node.right.bmin, node.right.bmax, leaf.bmin, leaf.bmax))
            node = node.left if cost_left <= cost_right else node.right
        # 为 node 与 leaf 建新的父节点
        parent = _Node()
        parent.bmin, parent.bmax = union_bounds(
            node.bmin, node.bmax, leaf.bmin, leaf.bmax)
        parent.left = node
        parent.right = leaf
        parent.parent = node.parent
        node.parent = parent
        leaf.parent = parent
        if parent.parent is None:
            self.root = parent
        else:
            if parent.parent.left is node:
                parent.parent.left = parent
            else:
                parent.parent.right = parent

    def _refit_leaf(self, leaf):
        bmin, bmax = tri_bounds(self.triangles[leaf.tris[0]])
        for i in leaf.tris[1:]:
            lo, hi = tri_bounds(self.triangles[i])
            bmin, bmax = union_bounds(bmin, bmax, lo, hi)
        leaf.bmin, leaf.bmax = bmin, bmax
        self._refit_up(leaf.parent)

    def _refit_up(self, node):
        while node is not None:
            node.bmin, node.bmax = union_bounds(
                node.left.bmin, node.left.bmax, node.right.bmin, node.right.bmax)
            node = node.parent

    # ------------------------------------------------------------------ 校验
    def check_invariants(self):
        """调试/自测用：验证每个叶子的包围盒恰好包住其三角形。"""
        def walk(node):
            if node.is_leaf:
                bmin, bmax = tri_bounds(self.triangles[node.tris[0]])
                for i in node.tris[1:]:
                    lo, hi = tri_bounds(self.triangles[i])
                    bmin, bmax = union_bounds(bmin, bmax, lo, hi)
                assert node.bmin == bmin and node.bmax == bmax
                return bmin, bmax
            lo_l, hi_l = walk(node.left)
            lo_r, hi_r = walk(node.right)
            bmin, bmax = union_bounds(lo_l, hi_l, lo_r, hi_r)
            assert node.bmin == bmin and node.bmax == bmax
            return bmin, bmax

        if self.root is not None:
            walk(self.root)
        assert len(self.leaf_of) == len(self.triangles)
