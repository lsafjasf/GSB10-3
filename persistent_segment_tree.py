#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可持久化线段树（Persistent Segment Tree / 主席树）

- 每次单点更新只复制根到叶子路径上的节点（O(log n) 个新节点），
  其余子树在所有版本之间共享，因此历史版本完全独立、互不干扰。
- 节点存储在数组中（不可变记录），版本即根节点下标，可任意保存与回查。
- 支持区间求和 / 区间最小值 / 区间最大值查询。
- 空区间（l == r）返回对应运算的单位元：sum=0, min=+inf, max=-inf。

运行自测:  python3 persistent_segment_tree.py
"""

from math import inf

__all__ = ["PersistentSegmentTree"]


class PersistentSegmentTree:
    """可持久化线段树，下标区间为 [0, n)，半开区间查询 [l, r)。"""

    __slots__ = ("n", "_left", "_right", "_sum", "_min", "_max", "_root0")

    def __init__(self, data):
        data = list(data)
        self.n = len(data)
        # 节点池：下标 0 固定为“空节点”（所有聚合值均为单位元）
        self._left = [0]
        self._right = [0]
        self._sum = [0]
        self._min = [inf]
        self._max = [-inf]
        self._root0 = self._build(0, self.n, data) if self.n else 0

    # ------------------------------------------------------------------ 构建
    def _new_node(self, left, right, agg_sum, agg_min, agg_max):
        self._left.append(left)
        self._right.append(right)
        self._sum.append(agg_sum)
        self._min.append(agg_min)
        self._max.append(agg_max)
        return len(self._sum) - 1

    def _build(self, lo, hi, data):
        if hi - lo == 1:
            v = data[lo]
            return self._new_node(0, 0, v, v, v)
        mid = (lo + hi) // 2
        left = self._build(lo, mid, data)
        right = self._build(mid, hi, data)
        return self._new_node(
            left,
            right,
            self._sum[left] + self._sum[right],
            min(self._min[left], self._min[right]),
            max(self._max[left], self._max[right]),
        )

    # ------------------------------------------------------------------ 更新
    def update(self, root, index, value):
        """在 root 版本上将 data[index] 设为 value，返回新版本根节点。

        只新建路径上的 O(log n) 个节点，旧版本完全不受影响。
        """
        if not 0 <= index < self.n:
            raise IndexError(f"index {index} out of range [0, {self.n})")
        return self._update(root, 0, self.n, index, value)

    def _update(self, node, lo, hi, index, value):
        if hi - lo == 1:
            return self._new_node(0, 0, value, value, value)
        mid = (lo + hi) // 2
        left, right = self._left[node], self._right[node]
        if index < mid:
            left = self._update(left, lo, mid, index, value)
        else:
            right = self._update(right, mid, hi, index, value)
        return self._new_node(
            left,
            right,
            self._sum[left] + self._sum[right],
            min(self._min[left], self._min[right]),
            max(self._max[left], self._max[right]),
        )

    # ------------------------------------------------------------------ 查询
    def query(self, root, l, r):
        """查询区间 [l, r)，返回 (sum, min, max)。空区间返回单位元。"""
        if not (0 <= l <= r <= self.n):
            raise ValueError(f"invalid range [{l}, {r}) for n={self.n}")
        if l == r:
            return (0, inf, -inf)
        return self._query(root, 0, self.n, l, r)

    def _query(self, node, lo, hi, l, r):
        if node == 0 or l <= lo and hi <= r:
            return (self._sum[node], self._min[node], self._max[node])
        mid = (lo + hi) // 2
        s_sum, s_min, s_max = 0, inf, -inf
        if l < mid:
            q = self._query(self._left[node], lo, mid, l, r)
            s_sum += q[0]
            s_min = min(s_min, q[1])
            s_max = max(s_max, q[2])
        if r > mid:
            q = self._query(self._right[node], mid, hi, l, r)
            s_sum += q[0]
            s_min = min(s_min, q[1])
            s_max = max(s_max, q[2])
        return (s_sum, s_min, s_max)

    def range_sum(self, root, l, r):
        return self.query(root, l, r)[0]

    def range_min(self, root, l, r):
        return self.query(root, l, r)[1]

    def range_max(self, root, l, r):
        return self.query(root, l, r)[2]

    # ------------------------------------------------------------------ 元信息
    @property
    def initial_root(self):
        """初始版本（由构造数据建成）的根节点。"""
        return self._root0

    @property
    def node_count(self):
        """已分配节点总数（含下标 0 的空节点）。"""
        return len(self._sum)


# ====================================================================== 自测
def _brute(data, l, r):
    seg = data[l:r]
    return (sum(seg), min(seg) if seg else inf, max(seg) if seg else -inf)


def _check(cond, msg):
    assert cond, msg


def test_basic():
    tree = PersistentSegmentTree([5, 2, 7, 1, 9, 3])
    root = tree.initial_root
    _check(tree.query(root, 0, 6) == (27, 1, 9), "whole range")
    _check(tree.query(root, 1, 4) == (10, 1, 7), "sub range")
    _check(tree.query(root, 3, 4) == (1, 1, 1), "single element")
    root2 = tree.update(root, 3, 100)  # 1 -> 100
    _check(tree.query(root2, 0, 6) == (126, 2, 100), "after update")
    _check(tree.query(root2, 3, 4) == (100, 100, 100), "updated point")
    # 旧版本不受影响
    _check(tree.query(root, 0, 6) == (27, 1, 9), "old version intact")
    _check(tree.query(root, 3, 4) == (1, 1, 1), "old point intact")
    print("ok - basic update/query")


def test_randomized_vs_bruteforce():
    """随机单点更新 + 随机区间查询，与逐元素暴力计算逐一比对。"""
    import random

    rng = random.Random(20261004)
    n = 200
    data = [rng.randint(-1000, 1000) for _ in range(n)]
    tree = PersistentSegmentTree(data)
    versions = [(tree.initial_root, list(data))]

    for step in range(500):
        base_root, base_data = versions[rng.randrange(len(versions))]
        idx = rng.randrange(n)
        val = rng.randint(-1000, 1000)
        new_root = tree.update(base_root, idx, val)
        new_data = list(base_data)
        new_data[idx] = val
        versions.append((new_root, new_data))

        # 每次更新后抽查若干历史版本（含刚产生的）
        for _ in range(6):
            root, expect = versions[rng.randrange(len(versions))]
            l = rng.randrange(n + 1)
            r = rng.randrange(l, n + 1)  # 允许 l == r（空区间）
            got = tree.query(root, l, r)
            want = _brute(expect, l, r)
            _check(
                got == want,
                f"step={step} range=[{l},{r}) got={got} want={want}",
            )
    print(f"ok - randomized vs brute force (500 updates, {len(versions)} versions)")
    return tree, versions


def test_version_independence():
    """大量历史版本：全部保留，最后统一校验每个版本都与自己的快照一致。"""
    import random

    rng = random.Random(7)
    n = 64
    data = [rng.randint(-50, 50) for _ in range(n)]
    tree = PersistentSegmentTree(data)
    versions = [(tree.initial_root, list(data))]
    for _ in range(2000):
        root, snapshot = versions[rng.randrange(len(versions))]
        idx = rng.randrange(n)
        val = rng.randint(-50, 50)
        new_root = tree.update(root, idx, val)
        new_snapshot = list(snapshot)
        new_snapshot[idx] = val
        versions.append((new_root, new_snapshot))

    # 事后统一断言：任意历史版本的查询结果 == 该版本快照的逐元素计算
    for root, snapshot in versions:
        for _ in range(3):
            l = rng.randrange(n + 1)
            r = rng.randrange(l, n + 1)
            _check(
                tree.query(root, l, r) == _brute(snapshot, l, r),
                "historical version diverged from its snapshot",
            )
    print(f"ok - version independence ({len(versions)} versions all verified)")


def test_node_growth():
    """节点增长数据：每次更新只新建路径上的节点。"""
    from math import ceil, log2

    print("ok - node growth (nodes created per update):")
    print(f"  {'n':>8} {'updates':>8} {'total_nodes':>12} "
          f"{'new/update':>10} {'ceil(log2 n)+1':>15}")
    for n in (1, 8, 100, 1024, 100000):
        tree = PersistentSegmentTree([0] * n)
        root = tree.initial_root
        before = tree.node_count
        updates = 200
        for i in range(updates):
            root = tree.update(root, i % n, i)
        grown = tree.node_count - before
        per = grown / updates
        bound = ceil(log2(n)) + 1 if n > 1 else 1
        _check(grown <= updates * bound, "growth exceeds O(log n) bound")
        if n & (n - 1) == 0:  # n 为 2 的幂时，每次更新恰好新建 log2(n)+1 个节点
            _check(grown == updates * bound, "exact growth mismatch for pow2 n")
        print(f"  {n:>8} {updates:>8} {tree.node_count:>12} "
              f"{per:>10.1f} {bound:>15}")

    # 共享性断言：新版本的未修改子树与旧版本复用同一批节点
    tree = PersistentSegmentTree([0] * 1024)
    r0 = tree.initial_root
    before = tree.node_count
    r1 = tree.update(r0, 0, 1)
    _check(tree.node_count - before == 11, "pow2 path length should be log2(1024)+1=11")
    print("ok - only path nodes are copied (1024 leaves -> 11 new nodes/update)")


def test_edge_cases():
    # 空数组
    empty = PersistentSegmentTree([])
    _check(empty.query(empty.initial_root, 0, 0) == (0, inf, -inf), "empty tree")
    try:
        empty.update(empty.initial_root, 0, 1)
        raise SystemExit("update on empty tree should raise")
    except IndexError:
        pass

    # 单元素
    one = PersistentSegmentTree([42])
    r0 = one.initial_root
    _check(one.query(r0, 0, 1) == (42, 42, 42), "single element")
    _check(one.query(r0, 0, 0) == (0, inf, -inf), "empty range on single element")
    r1 = one.update(r0, 0, -7)
    _check(one.query(r1, 0, 1) == (-7, -7, -7), "update single element")
    _check(one.query(r0, 0, 1) == (42, 42, 42), "old single-element version intact")

    # 空区间单位元
    tree = PersistentSegmentTree([3, 1, 4, 1, 5])
    root = tree.initial_root
    for i in range(6):
        _check(tree.query(root, i, i) == (0, inf, -inf), f"empty range at {i}")

    # 全区间 / 贴边区间
    _check(tree.query(root, 0, 5) == (14, 1, 5), "full range")
    _check(tree.query(root, 0, 1) == (3, 3, 3), "left edge")
    _check(tree.query(root, 4, 5) == (5, 5, 5), "right edge")

    # 非法区间与越界更新
    for bad in ((-1, 2), (0, 6), (3, 2)):
        try:
            tree.query(root, *bad)
            raise SystemExit(f"range {bad} should raise")
        except ValueError:
            pass
    try:
        tree.update(root, 5, 0)
        raise SystemExit("out-of-range update should raise")
    except IndexError:
        pass

    # 同一位置反复更新，旧版本链仍然各自正确
    r = tree.initial_root
    roots = [r]
    for v in (10, 20, 30):
        r = tree.update(r, 2, v)
        roots.append(r)
    _check(tree.range_sum(roots[0], 0, 5) == 14, "chain v0")
    _check(tree.range_sum(roots[1], 0, 5) == 20, "chain v1")
    _check(tree.range_sum(roots[2], 0, 5) == 30, "chain v2")
    _check(tree.range_sum(roots[3], 0, 5) == 40, "chain v3")
    print("ok - edge cases (empty tree/range, single element, invalid args)")


def run_all_tests():
    test_basic()
    test_randomized_vs_bruteforce()
    test_version_independence()
    test_node_growth()
    test_edge_cases()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    run_all_tests()
