"""BVH（层次包围盒）空间索引。

- 建树：沿质心包围盒最长轴做 12-bin SAH（表面面积启发式）切分。
- 查询：射线命中 / AABB 覆盖，对命中的包围盒子树直接整体收集。
- 增量更新：物体移动后重算所在叶子包围盒并沿父链 refit，拓扑不变；
  也支持 rebuild() 按新分布整体重建。

节点存放在扁平数组中（父 id 恒小于子 id），查询用栈迭代完成。
"""

from .geometry import (
    aabb_of_triangle,
    centroid,
    surface_area,
    ray_triangle,
    ray_aabb,
    aabb_triangle_overlap,
)

_BINS = 12
_LEAF_TRIANGLES = 4


class BVHNode:
    __slots__ = ("bmin", "bmax", "left", "right", "parent", "ids", "count")

    def __init__(self):
        self.bmin = None
        self.bmax = None
        self.left = -1
        self.right = -1
        self.parent = -1
        self.ids = None  # 叶子：三角形在 triangles 数组中的下标
        self.count = 0


class BVH:
    def __init__(self, triangles, max_leaf=_LEAF_TRIANGLES):
        """triangles: [(id, (v0, v1, v2)), ...]，id 由调用方指定。"""
        self.triangles = list(triangles)
        self.max_leaf = max_leaf
        self.nodes = []
        self._bounds = [None] * len(self.triangles)
        self._centers = [None] * len(self.triangles)
        self._leaf_of = {}  # triangle slot -> 叶子节点 id
        if self.triangles:
            self._build()

    # ------------------------------------------------------------------ build

    def _build(self):
        self.nodes = []
        tris = self.triangles
        n = len(tris)
        self._bounds = [None] * n
        self._centers = [None] * n
        self._leaf_of = {}
        for i, (_, verts) in enumerate(tris):
            self._bounds[i] = aabb_of_triangle(*verts)
            self._centers[i] = centroid(*verts)
        self._build_rec(list(range(n)), -1)

    def _union_bounds(self, indices):
        bmin = [float("inf")] * 3
        bmax = [float("-inf")] * 3
        for i in indices:
            lo, hi = self._bounds[i]
            for k in range(3):
                if lo[k] < bmin[k]:
                    bmin[k] = lo[k]
                if hi[k] > bmax[k]:
                    bmax[k] = hi[k]
        return tuple(bmin), tuple(bmax)

    def _make_leaf(self, indices, node, node_id):
        node.ids = indices
        node.count = len(indices)
        for slot in indices:
            self._leaf_of[slot] = node_id

    def _build_rec(self, indices, parent):
        node = BVHNode()
        node_id = len(self.nodes)
        self.nodes.append(node)  # 先占位，子节点 id 恒大于父节点
        node.parent = parent
        node.bmin, node.bmax = self._union_bounds(indices)
        n = len(indices)

        if n <= self.max_leaf:
            self._make_leaf(indices, node, node_id)
            return node_id

        # 质心包围盒，选最长轴
        cmin = [float("inf")] * 3
        cmax = [float("-inf")] * 3
        for i in indices:
            c = self._centers[i]
            for k in range(3):
                if c[k] < cmin[k]:
                    cmin[k] = c[k]
                if c[k] > cmax[k]:
                    cmax[k] = c[k]
        axis = 0
        for k in range(1, 3):
            if cmax[k] - cmin[k] > cmax[axis] - cmin[axis]:
                axis = k
        extent = cmax[axis] - cmin[axis]
        if extent <= 0.0:  # 所有质心重合，无法切分
            self._make_leaf(indices, node, node_id)
            return node_id

        # 12-bin SAH
        total_area = surface_area(node.bmin, node.bmax)
        bin_count = [0] * _BINS
        bin_bmin = [[float("inf")] * 3 for _ in range(_BINS)]
        bin_bmax = [[float("-inf")] * 3 for _ in range(_BINS)]
        scale = _BINS / (extent + 1e-30)
        for i in indices:
            b = int((self._centers[i][axis] - cmin[axis]) * scale)
            if b >= _BINS:
                b = _BINS - 1
            bin_count[b] += 1
            blo, bhi = self._bounds[i]
            row_min = bin_bmin[b]
            row_max = bin_bmax[b]
            for k in range(3):
                if blo[k] < row_min[k]:
                    row_min[k] = blo[k]
                if bhi[k] > row_max[k]:
                    row_max[k] = bhi[k]

        left_area = [0.0] * (_BINS - 1)
        right_area = [0.0] * (_BINS - 1)
        left_count = [0] * (_BINS - 1)
        right_count = [0] * (_BINS - 1)
        amin = [float("inf")] * 3
        amax = [float("-inf")] * 3
        count = 0
        for b in range(_BINS - 1):
            count += bin_count[b]
            left_count[b] = count
            for k in range(3):
                if bin_bmin[b][k] < amin[k]:
                    amin[k] = bin_bmin[b][k]
                if bin_bmax[b][k] > amax[k]:
                    amax[k] = bin_bmax[b][k]
            left_area[b] = surface_area(amin, amax) if count else 0.0
        amin = [float("inf")] * 3
        amax = [float("-inf")] * 3
        count = 0
        for b in range(_BINS - 1, 0, -1):
            count += bin_count[b]
            right_count[b - 1] = count
            for k in range(3):
                if bin_bmin[b][k] < amin[k]:
                    amin[k] = bin_bmin[b][k]
                if bin_bmax[b][k] > amax[k]:
                    amax[k] = bin_bmax[b][k]
            right_area[b - 1] = surface_area(amin, amax) if count else 0.0

        best_cost = n * total_area  # 不切分（全放叶子）的代价
        best_split = -1
        for b in range(_BINS - 1):
            cost = left_count[b] * left_area[b] + right_count[b] * right_area[b]
            if cost < best_cost:
                best_cost = cost
                best_split = b

        if best_split < 0:
            self._make_leaf(indices, node, node_id)
            return node_id

        split_coord = cmin[axis] + (best_split + 1) * extent / _BINS
        left = [i for i in indices if self._centers[i][axis] < split_coord]
        left_set = set(left)
        right = [i for i in indices if i not in left_set]
        if not left or not right:  # SAH 退化保护：退回中位数切分
            ordered = sorted(indices, key=lambda i: self._centers[i][axis])
            mid = n // 2
            left, right = ordered[:mid], ordered[mid:]

        node.left = self._build_rec(left, node_id)
        node.right = self._build_rec(right, node_id)
        return node_id

    # -------------------------------------------------------------- updating

    def move_triangle(self, slot, verts):
        """更新 slot 处三角形的顶点，重算叶子包围盒并沿父链 refit。"""
        self.triangles[slot] = (self.triangles[slot][0], verts)
        self._bounds[slot] = aabb_of_triangle(*verts)
        self._centers[slot] = centroid(*verts)
        leaf_id = self._leaf_of.get(slot)
        if leaf_id is None:
            return
        leaf = self.nodes[leaf_id]
        leaf.bmin, leaf.bmax = self._union_bounds(leaf.ids)
        node_id = leaf.parent
        while node_id >= 0:
            nd = self.nodes[node_id]
            lc = self.nodes[nd.left]
            rc = self.nodes[nd.right]
            nd.bmin = (
                min(lc.bmin[0], rc.bmin[0]),
                min(lc.bmin[1], rc.bmin[1]),
                min(lc.bmin[2], rc.bmin[2]),
            )
            nd.bmax = (
                max(lc.bmax[0], rc.bmax[0]),
                max(lc.bmax[1], rc.bmax[1]),
                max(lc.bmax[2], rc.bmax[2]),
            )
            node_id = nd.parent

    def rebuild(self):
        """整体重建（拓扑按移动后的分布重新优化）。"""
        self._build()

    # --------------------------------------------------------------- queries

    def ray_query(self, orig, dir):
        """返回所有命中 (id, t)，按 t 升序。与暴力检测共用同一求交函数。"""
        if not self.nodes:
            return []
        hits = []
        stack = [0]
        nodes = self.nodes
        tris = self.triangles
        while stack:
            node = nodes[stack.pop()]
            if not ray_aabb(orig, dir, node.bmin, node.bmax):
                continue
            if node.count:
                for i in node.ids:
                    v0, v1, v2 = tris[i][1]
                    t = ray_triangle(orig, dir, v0, v1, v2)
                    if t is not None:
                        hits.append((tris[i][0], t))
            else:
                stack.append(node.right)
                stack.append(node.left)
        hits.sort(key=lambda h: (h[1], h[0]))
        return hits

    def nearest_hit(self, orig, dir):
        """最近命中 (id, t)，未命中返回 None。"""
        hits = self.ray_query(orig, dir)
        return hits[0] if hits else None

    def box_query(self, bmin, bmax):
        """返回与 AABB 相交的所有三角形 id 集合。"""
        if not self.nodes:
            return set()
        result = set()
        stack = [0]
        collect = []
        nodes = self.nodes
        tris = self.triangles
        while stack:
            node = nodes[stack.pop()]
            # 与节点包围盒不相交 → 整棵子树跳过
            if (
                node.bmin[0] > bmax[0] or node.bmax[0] < bmin[0]
                or node.bmin[1] > bmax[1] or node.bmax[1] < bmin[1]
                or node.bmin[2] > bmax[2] or node.bmax[2] < bmin[2]
            ):
                continue
            # 查询盒完全包住节点 → 子树整体收集，无需逐三角形测试
            if (
                node.bmin[0] >= bmin[0] and node.bmax[0] <= bmax[0]
                and node.bmin[1] >= bmin[1] and node.bmax[1] <= bmax[1]
                and node.bmin[2] >= bmin[2] and node.bmax[2] <= bmax[2]
            ):
                collect.append(node)
                while collect:
                    sub = collect.pop()
                    if sub.count:
                        for i in sub.ids:
                            result.add(tris[i][0])
                    else:
                        collect.append(nodes[sub.left])
                        collect.append(nodes[sub.right])
                continue
            if node.count:
                for i in node.ids:
                    v0, v1, v2 = tris[i][1]
                    if aabb_triangle_overlap(bmin, bmax, v0, v1, v2):
                        result.add(tris[i][0])
            else:
                stack.append(node.right)
                stack.append(node.left)
        return result

    # ------------------------------------------------------------------ stats

    def stats(self):
        leaves = [n for n in self.nodes if n.count]
        depths = []

        def depth(nid):
            d = 0
            while self.nodes[nid].parent >= 0:
                nid = self.nodes[nid].parent
                d += 1
            return d

        for nid, n in enumerate(self.nodes):
            if n.count:
                depths.append(depth(nid))
        return {
            "nodes": len(self.nodes),
            "leaves": len(leaves),
            "max_leaf_tris": max((n.count for n in leaves), default=0),
            "max_depth": max(depths, default=0),
        }
