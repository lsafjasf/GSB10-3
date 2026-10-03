"""PersistentRangeTree 自测脚本（纯标准库，直接运行即可）。

运行：python3 selftest_persistent_range_tree.py
内容：
1. 基础单点更新 + 区间查询，与逐元素计算逐一对照；
2. 版本独立性断言（链式版本 + 分支版本）；
3. 大量历史版本随机压力测试（3000 个版本）；
4. 边界用例：空区间、单元素域、整段查询、越界参数、size=0；
5. 节点增长实测（2^k 规模与非二次幂规模）。
"""

import math
import random
import time

from persistent_range_tree import EMPTY_RESULT, PersistentRangeTree

PASS = 0


def check(condition, message):
    global PASS
    assert condition, message
    PASS += 1


def reference_query(arr, left, right):
    """逐元素计算的参考结果：(sum, min, max)，空区间返回单位元。"""
    if left >= right:
        return EMPTY_RESULT
    segment = arr[left:right]
    return sum(segment), min(segment), max(segment)


def test_basic_updates_and_queries():
    tree = PersistentRangeTree(8)
    arr = [0] * 8
    rng = random.Random(42)

    version = 0
    for i in range(200):
        pos = rng.randrange(8)
        value = rng.randint(-1000, 1000)
        version = tree.update(version, pos, value)
        arr[pos] = value

    # 全区间 / 前缀 / 后缀 / 任意子区间 / 单点 / 空区间，全部与参考实现对照。
    bounds = [(0, 8), (0, 1), (7, 8), (2, 6), (3, 4), (5, 5), (0, 0)]
    for left, right in bounds:
        check(tree.query(version, left, right)
              == reference_query(arr, left, right),
              f"区间 [{left},{right}) 查询与逐元素结果不一致")

    # 初始零值版本查询。
    check(tree.query(0, 0, 8) == (0, 0, 0), "初始版本应为全零")
    print("  基础更新/查询：通过（含与逐元素计算的对照）")


def test_version_independence():
    # 链式历史：每次更新产生一个新版本，所有旧版本的结果都必须保持不变。
    tree = PersistentRangeTree(10)
    rng = random.Random(7)
    history = [[0] * 10]
    version = 0
    for _ in range(60):
        arr = list(history[version])
        pos = rng.randrange(10)
        value = rng.randint(-50, 50)
        version = tree.update(version, pos, value)
        arr[pos] = value
        history.append(arr)

    # 断言 1：生成全部后续版本之后，逐版本、逐区间回查旧版本。
    for ver, arr in enumerate(history):
        for left in range(11):
            for right in range(left, 11):
                check(tree.query(ver, left, right)
                      == reference_query(arr, left, right),
                      f"链式版本 {ver} 区间 [{left},{right}) 被后续版本污染")

    # 断言 2：从同一个旧版本分叉出两条历史，分支之间互不影响。
    base = tree.update(0, 2, 111)
    branch_a = tree.update(base, 0, 10)
    branch_b = tree.update(base, 0, 20)
    branch_a2 = tree.update(branch_a, 1, 99)

    check(tree.query(base, 0, 10)[0] == 111, "分叉基点值错误")
    check(tree.query(branch_a, 0, 10)[0] == 121, "分支 A 值错误")
    check(tree.query(branch_b, 0, 10)[0] == 131, "分支 B 值错误")
    check(tree.query(branch_a2, 0, 10)[0] == 220, "分支 A 后继值错误")
    # branch_b 不应看到 branch_a/branch_a2 的写入。
    check(tree.query(branch_b, 0, 10) == (131, 0, 111),
          "分支 B 被分支 A 污染")
    # 初始版本仍为全零。
    check(tree.query(0, 0, 10) == (0, 0, 0), "初始版本被污染")

    # 断言 3：旧版本根编号在后续更新中不被复用或改写（根不可变）。
    roots_before = tree.roots()
    tree.update(branch_a2, 9, -7)
    tree.update(branch_b, 9, -8)
    check(tree.roots()[:len(roots_before)] == roots_before,
          "旧版本根节点被修改或复用")
    print("  版本独立性：通过（链式历史 + 分叉历史 + 根不可变断言）")


def test_many_versions():
    n = 99991
    tree = PersistentRangeTree(n)
    rng = random.Random(2024)
    version = 0
    snapshots = {}
    # 只保留少量快照数组用于校验，避免参考数据本身撑爆内存。
    snapshot_versions = {0, 1, 250, 1000, 2999}

    # 用一个当前数组做逐元素计算基准（随版本推进）。
    arr = [0] * n
    start = time.time()
    for step in range(3000):
        pos = rng.randrange(n)
        value = rng.randint(-1_000_000, 1_000_000)
        version = tree.update(version, pos, value)
        arr[pos] = value
        if version in snapshot_versions:
            snapshots[version] = list(arr)
    elapsed = time.time() - start

    check(version == 3000, "版本号应为 3000")
    # 在最新版本和各快照版本上做随机区间抽查，与逐元素结果对照。
    check_rng = random.Random(99)
    for ver in sorted(snapshots):
        snap = snapshots[ver]
        for _ in range(40):
            left = check_rng.randrange(n)
            right = check_rng.randrange(left, n + 1)
            check(tree.query(ver, left, right)
                  == reference_query(snap, left, right),
                  f"压力测试版本 {ver} 区间 [{left},{right}) 结果不一致")
    print(f"  大量历史版本：通过（3000 个版本 / 域大小 {n}，"
          f"更新耗时 {elapsed:.2f}s）")


def test_edge_cases():
    # 空区间单位元。
    tree = PersistentRangeTree(4)
    v = tree.update(0, 1, 5)
    check(tree.query(v, 2, 2) == EMPTY_RESULT, "空区间应返回单位元")
    check(tree.query(v, 0, 0) == EMPTY_RESULT, "空区间应返回单位元")

    # 空区间的 min/max 与任何非空区间合并时满足单位元性质。
    nonempty = tree.query(v, 0, 4)
    check(min(EMPTY_RESULT[1], nonempty[1]) == nonempty[1], "min 单位元错误")
    check(max(EMPTY_RESULT[2], nonempty[2]) == nonempty[2], "max 单位元错误")

    # 单元素域：多次更新同一位置，每次都产生独立版本。
    tiny = PersistentRangeTree(1)
    versions = [0]
    for value in (3, -8, 42, 0):
        versions.append(tiny.update(versions[-1], 0, value))
    for ver, value in zip(versions, [0, 3, -8, 42, 0]):
        check(tiny.query(ver, 0, 1) == (value, value, value),
              "单元素域历史版本值错误")
        check(tiny.query(ver, 0, 0) == EMPTY_RESULT, "单元素域空区间错误")

    # 整段查询、全负/全正最值、浮点值。
    f = PersistentRangeTree(5)
    vals = [1.5, -2.25, 3.0, -0.5, 10.0]
    fv = 0
    for i, value in enumerate(vals):
        fv = f.update(fv, i, value)
    check(f.query(fv, 0, 5)
          == (sum(vals), min(vals), max(vals)), "浮点整段聚合错误")
    check(f.query(fv, 1, 4)
          == reference_query(vals, 1, 4), "浮点子区间错误")

    # size=0：只允许空区间查询，任何更新或非空查询都应报错。
    zero = PersistentRangeTree(0)
    check(zero.size == 0 and zero.version_count == 1, "size=0 初始化错误")
    check(zero.query(0, 0, 0) == EMPTY_RESULT, "size=0 空区间查询错误")

    def expect_error(exc_type, fn):
        try:
            fn()
        except exc_type:
            return
        raise AssertionError(f"应当抛出 {exc_type.__name__}")

    expect_error(IndexError, lambda: zero.update(0, 0, 1))
    expect_error(IndexError, lambda: zero.query(0, 0, 1))
    expect_error(IndexError, lambda: tree.query(v, -1, 2))
    expect_error(IndexError, lambda: tree.query(v, 0, 5))
    expect_error(IndexError, lambda: tree.update(99, 0, 1))
    expect_error(IndexError, lambda: tree.query(99, 0, 1))
    expect_error(IndexError, lambda: tree.update(v, 4, 1))
    expect_error(ValueError, lambda: PersistentRangeTree(-1))
    expect_error(TypeError, lambda: tree.update(v, 0.5, 1))

    print("  边界用例：通过（空区间 / 单元素 / size=0 / 越界 / 浮点）")


def measure_growth_power_of_two():
    """n = 2^k 时每次更新恰好新增 k+1 个节点（根到叶子的路径长度）。"""
    n = 131072  # 2^17
    tree = PersistentRangeTree(n)
    rng = random.Random(123)
    checkpoints = [1, 2, 5, 10, 20, 50, 100, 200, 500,
                   1000, 2000, 5000, 10000, 20000]
    rows = []
    version = 0
    prev_step = 0
    nodes_prev = 1
    for step in range(1, max(checkpoints) + 1):
        version = tree.update(version, rng.randrange(n), rng.randint(0, 10**9))
        if step in checkpoints:
            nodes = tree.node_count
            delta = nodes - 1  # 去掉空节点 0 号
            avg_per_update = delta / step
            full_copy = step * n
            rows.append((step, nodes - 1, nodes - nodes_prev,
                         avg_per_update, full_copy / max(delta, 1)))
            prev_step = step
            nodes_prev = nodes

    depth = int(math.log2(n)) + 1
    # 每次更新节点增量恒定等于树高；即使同一位置反复更新也一样（路径整体克隆）。
    check(all(row[2] == depth * (row[0] - (rows[i - 1][0] if i else 0))
              for i, row in enumerate(rows)),
          "二次幂规模下每次更新节点增量应恒定")
    return rows, depth, n


def measure_growth_general(n=99991, samples=64):
    """非二次幂规模：路径长度只有 floor(log2 n)+1 或 ceil(log2 n)+1 两种。"""
    tree = PersistentRangeTree(n)
    rng = random.Random(55)
    positions = [i * n // samples for i in range(samples)]
    deltas = []
    version = 0
    for pos in positions:
        before = tree.node_count
        version = tree.update(version, pos, rng.randint(0, 10**9))
        deltas.append(tree.node_count - before)
    lo = math.floor(math.log2(n)) + 1
    hi = math.ceil(math.log2(n)) + 1
    check(all(lo <= d <= hi for d in deltas),
          f"路径长度应落在 [{lo}, {hi}] 之间")
    return deltas, lo, hi


def test_node_growth_report():
    rows, depth, n = measure_growth_power_of_two()
    deltas, lo, hi = measure_growth_general()

    print("  节点增长：通过")
    print()
    print(f"  【节点增长数据】下标域 n = {n}（= 2^17），树高（路径节点数）= {depth}")
    print("  更新次数 | 累计新增节点 | 本段新增 | 平均每次新增 "
          "| 相对整树复制节省倍数")
    for updates, total, segment_delta, avg, saving in rows:
        print(f"  {updates:>8} | {total:>12} | {segment_delta:>8} | "
              f"{avg:>12.2f} | {saving:>20.1f}x")
    avg_delta = sum(deltas) / len(deltas)
    print()
    print(f"  非二次幂域 n = 99991：路径长度分布在 [{lo}, {hi}]，"
          f"抽样 {len(deltas)} 次平均 {avg_delta:.2f} 个节点/次更新")
    print("  结论：节点总数 = 1 + O(更新次数 * log2(n))，"
          "与整树复制的 O(更新次数 * n) 相比是对数级增长。")


def main():
    print("== 可持久化区间树自测 ==")
    test_basic_updates_and_queries()
    test_version_independence()
    test_many_versions()
    test_edge_cases()
    test_node_growth_report()
    print()
    print(f"全部断言通过（共 {PASS} 项断言）。")


if __name__ == "__main__":
    main()
