"""LCA 库的边界用例、对拍自测与耗时基准。

运行：
    python3 test_lca.py            # 自测 + 对拍 + 默认基准
    python3 test_lca.py --bench 200000
退出码非 0 表示有用例失败。
"""

import argparse
import random
import sys
import time

from lca import Tree, NaiveTree, build_from_edges, _NO_EDGE

PASS = 0


def check(cond, msg):
    global PASS
    if not cond:
        raise AssertionError(msg)
    PASS += 1


# ---------------------------------------------------------------------- #
# 1. 边界用例
# ---------------------------------------------------------------------- #
def test_single_node():
    t = Tree()
    t.add_node("only")
    t.build_index()
    lca, dist, mx = t.query("only", "only")
    check(lca == "only", "单节点 LCA 应是自身")
    check(dist == 0, "单节点距离应为 0")
    check(mx == _NO_EDGE, "单节点路径无最大边权，应为 -inf")
    print("[ok] 单节点树")


def test_chain():
    n = 1000
    t = Tree()
    t.add_node(0)
    for i in range(1, n):
        t.add_node(i, parent=i - 1, weight=i)
    t.build_index()
    lca, dist, mx = t.query(0, n - 1)
    check(lca == 0, "链两端 LCA 应为链首")
    check(dist == n - 1, "链两端距离应为 N-1")
    check(mx == n - 1, "链 0..N-1 路径最大边权应为 N-1")
    lca, dist, _ = t.query(n - 1, 300)
    check(lca == 300 and dist == n - 1 - 300, "链上祖先-后代查询错误")
    # 每个 2^k 跳跃点都要正确
    lca, dist, mx = t.query(0, 512)
    check((lca, dist, mx) == (0, 512, 512), "链上 2 的幂距离查询错误")
    print("[ok] 链状树（N=1000，含 2 的幂跳跃点）")


def test_star():
    t = Tree()
    t.add_node("center")
    for i in range(500):
        t.add_node(f"leaf{i}", parent="center", weight=i + 1)
    t.build_index()
    lca, dist, mx = t.query("leaf0", "leaf499")
    check(lca == "center", "星形：两叶子 LCA 为中心")
    check(dist == 2, "星形：两叶子距离为 2")
    check(mx == 500, "星形：最大边权为 max(1,500)=500")
    lca, dist, _ = t.query("center", "leaf123")
    check(lca == "center" and dist == 1, "星形：中心与叶子的查询错误")
    print("[ok] 星形树（500 个叶子）")


def test_self_as_ancestor():
    t = Tree()
    t.add_node("r")
    t.add_node("a", parent="r", weight=5)
    t.add_node("b", parent="a", weight=7)
    t.build_index()
    for x in ("r", "a", "b"):
        lca, dist, mx = t.query(x, x)
        check(lca == x and dist == 0 and mx == _NO_EDGE, f"{x} 自身查询错误")
    check(t.lca("r", "b") == "r", "r 是 b 的祖先")
    check(t.lca("b", "r") == "r", "LCA 应与参数顺序无关")
    check(t.path_max_weight("b", "r") == 7, "祖先路径最值错误")
    print("[ok] 节点自身作为祖先 / 顺序无关")


def test_error_handling():
    t = Tree()
    t.add_node("r")
    try:
        t.add_node("r")
        raise AssertionError("重复节点应报错")
    except ValueError:
        pass
    try:
        t.add_node("x", parent="nope")
        raise AssertionError("父不存在应报错")
    except ValueError:
        pass
    t.add_node("a", parent="r", weight=1)
    t.build_index()
    t.add_node("b", parent="a", weight=2)  # 结构变化导致索引失效
    try:
        t.lca("r", "b")
        raise AssertionError("脏索引查询应报错")
    except RuntimeError:
        pass
    t2 = Tree()
    t2.add_node("r1")
    try:
        t2.add_node("r2")
        raise AssertionError("不支持第二个根/森林")
    except ValueError:
        pass
    print("[ok] 异常输入与脏索引保护")


def test_incremental():
    # 增量追加：与全量重建结果一致；树高溢出时自动转全量重建
    t_full = Tree()
    t_inc = Tree()
    t_full.add_node(0)
    t_inc.add_node(0)
    for i in range(1, 6):
        t_full.add_node(i, parent=i - 1, weight=i)
        t_inc.add_node(i, parent=i - 1, weight=i)
    t_full.build_index()
    t_inc.build_index()
    for i in range(6, 100):
        t_full.add_node(i, parent=i - 1, weight=i)
        t_inc.add_node(i, parent=i - 1, weight=i)
        t_inc.extend_index()
    t_full.build_index()
    rng = random.Random(7)
    for _ in range(2000):
        a, b = rng.randrange(100), rng.randrange(100)
        check(
            t_full.query(a, b) == t_inc.query(a, b),
            f"增量追加与全量重建不一致：{a},{b}",
        )
    # 树高超过 LOG 容量时 extend 自动重建（链首只有 LOG=1，加 1 个孩子即溢出）
    tiny = Tree()
    tiny.add_node("r")
    tiny.build_index()
    tiny.add_node("c", parent="r", weight=9)
    tiny.extend_index()
    check(tiny.query("r", "c") == ("r", 1, 9.0), "溢出重建后查询值错误")
    print("[ok] 增量 extend_index 与全量重建一致（含溢出自动重建）")


# ---------------------------------------------------------------------- #
# 2. 随机对拍：倍增实现 vs 逐层上溯参考实现
# ---------------------------------------------------------------------- #
def random_tree(n, seed):
    rng = random.Random(seed)
    t, ref = Tree(), NaiveTree()
    t.add_node(0)
    ref.add_node(0)
    for i in range(1, n):
        p = rng.randrange(i)
        w = rng.uniform(-100, 100)
        t.add_node(i, parent=p, weight=w)
        ref.add_node(i, parent=p, weight=w)
    return t, ref, rng


def test_differential():
    for n in (1, 2, 3, 10, 100, 300):
        t, ref, rng = random_tree(n, seed=42 + n)
        t.build_index()
        pairs = [(a, b) for a in range(n) for b in range(n)] if n <= 100 else None
        if pairs is None:
            pairs = [
                (rng.randrange(n), rng.randrange(n)) for _ in range(20000)
            ]
        for a, b in pairs:
            got = t.query(a, b)
            want = ref.query(a, b)
            check(
                got[0] == want[0] and got[1] == want[1]
                and (got[2] == want[2] or
                     (got[2] == _NO_EDGE and want[2] == _NO_EDGE)),
                f"对拍不一致 n={n} pair=({a},{b}) got={got} want={want}",
            )
    print("[ok] 随机对拍：6 种规模、全对/抽样共约 3.6 万对，全部一致")


# ---------------------------------------------------------------------- #
# 3. 耗时基准
# ---------------------------------------------------------------------- #
def make_chain(n):
    t = Tree()
    t.add_node(0)
    for i in range(1, n):
        t.add_node(i, parent=i - 1, weight=i)
    return t


def make_star(n):
    t = Tree()
    t.add_node(0)
    for i in range(1, n):
        t.add_node(i, parent=0, weight=i)
    return t


def make_random(n, seed=123):
    t, _, _ = random_tree(n, seed)
    return t


def bench():
    n = ARGS.bench
    q = 50000
    print(f"\n== 耗时基准（N={n}, 随机查询对={q}, Python {sys.version.split()[0]}） ==")
    print(f"{'形态':<8}{'预处理 build(s)':>16}{'每查询(µs)':>14}{"LOG":>6}")
    rng = random.Random(2024)
    pairs_big = [(rng.randrange(n), rng.randrange(n)) for _ in range(q)]
    for name, maker in (("链状", make_chain), ("星形", make_star), ("随机树", make_random)):
        t = maker(n)
        t0 = time.perf_counter()
        t.build_index()
        t_build = time.perf_counter() - t0
        t0 = time.perf_counter()
        for a, b in pairs_big:
            t.query(a, b)
        dt = time.perf_counter() - t0
        print(f"{name:<8}{t_build:>16.4f}{dt / q * 1e6:>14.2f}{t._LOG:>6}")
    print(f"预处理复杂度 O(N log N)，单次查询 O(log N)，理论上界 {int(n).bit_length()} 步")

    # 与朴素逐层上溯对比：小规模可见线性 vs 对数
    m = 10000
    ref_chain = NaiveTree()
    ref_chain.add_node(0)
    for i in range(1, m):
        ref_chain.add_node(i, parent=i - 1, weight=i)
    t0 = time.perf_counter()
    ref_chain.query(0, m - 1)
    naive_one = time.perf_counter() - t0
    fast_chain = make_chain(m).build_index()
    t0 = time.perf_counter()
    for _ in range(10000):
        fast_chain.query(0, m - 1)
    fast_avg = (time.perf_counter() - t0) / 10000
    print(
        f"\n链两端最坏查询对比（N={m}）：朴素逐层上溯 {naive_one * 1e3:.3f} ms/次"
        f"（{m} 步，O(N)），倍增 {fast_avg * 1e6:.1f} µs/次（O(log N)）"
    )


def main():
    test_single_node()
    test_chain()
    test_star()
    test_self_as_ancestor()
    test_error_handling()
    test_incremental()
    test_differential()
    print(f"\n全部 {PASS} 条断言通过")
    bench()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bench", type=int, default=100000, help="基准规模 N")
    ARGS = p.parse_args()
    main()
