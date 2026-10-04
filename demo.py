"""Runnable demonstration / acceptance script for propcheck.

Run:  python3 demo.py
"""

import random

from propcheck import Gen, check, for_all


def section(title):
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


# ---------------------------------------------------------------------------
# A recursive generator for nested trees:
#   tree = ("leaf", int)  |  ("node", [tree, ...])
# ---------------------------------------------------------------------------

def tree_gen(max_len=6):
    def build(size):
        if size <= 0:
            return Gen.ints(0, 9).map(lambda n: ("leaf", n))
        return Gen.frequency([
            (1, Gen.ints(0, 9).map(lambda n: ("leaf", n))),
            (3, Gen.list_of(Gen.lazy(lambda: build(size // 2)), max_len=max_len)
             .map(lambda children: ("node", children))),
        ])

    return Gen.sized(build)


def leaf_count(tree):
    tag, payload = tree
    if tag == "leaf":
        return 1
    return sum(leaf_count(child) for child in payload)


def node_count(tree):
    tag, payload = tree
    if tag == "leaf":
        return 1
    return 1 + sum(node_count(child) for child in payload)


def depth(tree):
    tag, payload = tree
    if tag == "leaf":
        return 1
    return 1 + max((depth(c) for c in payload), default=0)


def main():
    # -----------------------------------------------------------------------
    # 1. Size parameter vs. case complexity (measured relationship)
    # -----------------------------------------------------------------------
    section("1. size 与用例长度/复杂度的关系 (每个 size 采样 1000 次)")

    n_samples = 1000
    list_gen = Gen.list_of(Gen.ints(0, 9))
    print("\n[flat lists]  size -> list length (mean / min / max)")
    print("-" * 44)
    for size in (0, 1, 2, 5, 10, 20, 30):
        rng = random.Random(2026)
        lengths = [len(list_gen.generate(rng, size)) for _ in range(n_samples)]
        print("  size=%2d  mean=%6.3f  min=%d  max=%d"
              % (size, sum(lengths) / n_samples, min(lengths), max(lengths)))

    print("\n[nested trees]  size -> node count / depth (mean / max)")
    print("-" * 44)
    tree_g = tree_gen()
    for size in (0, 1, 2, 4, 8, 16, 32):
        rng = random.Random(2026)
        values = [tree_g.generate(rng, size) for _ in range(n_samples)]
        nodes = [node_count(v) for v in values]
        depths = [depth(v) for v in values]
        print("  size=%2d  nodes mean=%7.3f max=%4d | depth mean=%.3f max=%d"
              % (size, sum(nodes) / n_samples, max(nodes),
                 sum(depths) / n_samples, max(depths)))

    print("""
解释: list_of 的长度在 [0, size] 上均匀分布, 因此均值 ~ size/2 (线性关系);
嵌套树每层把子树的 size 减半, 所以最大深度 ~ log2(size)+1,
最大节点数 ~ max_len ** (log2(size)+1), 复杂度受 size 精确控制。""")

    # -----------------------------------------------------------------------
    # 2. Automatic shrinking: before / after comparison
    # -----------------------------------------------------------------------
    section("2. 失败用例自动收缩 (收缩前 vs 收缩后)")

    demos = [
        ("flat list: xs == sorted(xs)  [固定 size=20]",
         lambda xs: xs == sorted(xs),
         Gen.list_of(Gen.ints(-100, 100)), 5, 20),
        ("flat list: all(x < 10)  [默认 size 递增]",
         lambda xs: all(x < 10 for x in xs),
         Gen.list_of(Gen.ints(0, 100)), 3, None),
        ("nested tree: leaf_count(t) < 5  [默认 size 递增]",
         lambda t: leaf_count(t) < 5,
         tree_gen(), 2, None),
        ("object record: age < 18  [默认 size 递增]",
         lambda p: p["age"] < 18,
         Gen.object_({"name": Gen.const("alice"), "age": Gen.ints(0, 120)}),
         9, None),
    ]

    for title, prop, gen, seed, size in demos:
        result = check(prop, gen, tests=300, seed=seed, size=size)
        print("\n- %s  (seed=%d, 第 %d 个用例失败)"
              % (title, seed, result["tests_run"]))
        original = repr(result["original"])
        shrunk = repr(result["shrunk"])
        shown = original if len(original) <= 100 else original[:97] + "..."
        print("  收缩前 (%d 字符): %s" % (len(original), shown))
        print("  收缩后 (%d 字符): %s" % (len(shrunk), shrunk))
        print("  缩小比例: %d -> %d 个字符" % (len(original), len(shrunk)))

    # -----------------------------------------------------------------------
    # 3. Reproducibility: same seed => byte-identical reruns
    # -----------------------------------------------------------------------
    section("3. 随机种子可复现 (同一 seed 运行两次)")

    gen = Gen.list_of(Gen.ints(-50, 50))
    seq_a = [gen.generate(random.Random(4242), min(i, 30)) for i in range(100)]
    seq_b = [gen.generate(random.Random(4242), min(i, 30)) for i in range(100)]
    assert seq_a == seq_b, "same seed produced different value sequences"
    print("\n[断言 1] seed=4242 两次生成的 100 个输入序列完全一致: PASSED")
    print("  前 8 个输入:", seq_a[:8])

    def run_failure():
        return check(
            lambda xs: all(x < 10 for x in xs),
            Gen.list_of(Gen.ints(0, 100)),
            tests=200, seed=4242,
        )

    run1, run2 = run_failure(), run_failure()
    assert run1["tests_run"] == run2["tests_run"]
    assert repr(run1["original"]) == repr(run2["original"])
    assert repr(run1["shrunk"]) == repr(run2["shrunk"])
    print("[断言 2] 两次完整运行的失败位置/原始反例/最小反例全部一致: PASSED")
    print("  失败用例序号:", run1["tests_run"])
    print("  原始反例    :", repr(run1["original"]))
    print("  最小反例    :", repr(run1["shrunk"]))

    different = check(lambda xs: all(x < 10 for x in xs),
                      Gen.list_of(Gen.ints(0, 100)), tests=200, seed=4243)
    assert repr(different["original"]) != repr(run1["original"])
    print("[断言 3] seed=4243 与 seed=4242 的原始反例不同: PASSED")

    # -----------------------------------------------------------------------
    # 4. Boundary cases
    # -----------------------------------------------------------------------
    section("4. 边界用例")

    print("\n(a) 不变量成立: sort 幂等性, 500 个随机用例全部通过")
    ran = for_all(lambda xs: sorted(sorted(xs)) == sorted(xs),
                  Gen.list_of(Gen.ints()), tests=500, seed=1)
    print("    -> %d/500 通过, for_all 正常返回 %d" % (ran, ran))

    print("\n(b) 生成器产生空值 None / '' / []:")
    empty_gen = Gen.one_of([Gen.const(None), Gen.const(""),
                            Gen.list_of(Gen.ints(0, 9))])
    rng = random.Random(42)
    saw_none = saw_empty_str = saw_empty_list = False
    for size in range(60):
        value = empty_gen.generate(rng, size)
        saw_none = saw_none or value is None
        saw_empty_str = saw_empty_str or value == ""
        saw_empty_list = saw_empty_list or value == []
    assert saw_none and saw_empty_str and saw_empty_list
    print("    None: %s, '': %s, []: %s  [断言] 三类空值都被生成: PASSED"
          % (saw_none, saw_empty_str, saw_empty_list))

    print("\n(c) 非空失败列表一路收缩到退化输入 []:")
    # 性质: 非空且元素全为正; [] 和含 0 的列表都失败。
    result = check(
        lambda xs: bool(xs) and all(x > 0 for x in xs),
        Gen.list_of(Gen.ints(0, 9)), tests=50, seed=1, size=8,
    )
    print("    原始反例 =", repr(result["original"]),
          " 最小反例 =", repr(result["shrunk"]))
    assert result["original"] != [] and result["shrunk"] == []

    print("\n(d) 收缩到退化输入 0 / False:")
    r_zero = check(lambda x: x != 0, Gen.ints(0, 1000), tests=200, seed=8)
    r_bool = check(lambda b: b, Gen.booleans(), tests=50, seed=0)
    print("    x != 0 的最小反例:", repr(r_zero["shrunk"]))
    print("    b == True 的最小反例:", repr(r_bool["shrunk"]))
    assert r_zero["shrunk"] == 0 and r_bool["shrunk"] is False

    print("\n(e) min_len 约束下不能越过边界 ([int], min_len=1):")
    r_min = check(lambda xs: len(xs) > 1,
                  Gen.list_of(Gen.ints(0, 9), min_len=1), tests=100, seed=6)
    print("    最小反例:", repr(r_min["shrunk"]), "(长度没有被收缩到 0)")
    assert r_min["shrunk"] == [0]

    print("\n(f) 性质抛出非断言异常也会被捕获并收缩:")

    def raises(x):
        if x > 3:
            raise ValueError("x too big: %d" % x)
        return True

    r_exc = check(raises, Gen.ints(0, 100), tests=100, seed=0)
    print("    最小反例:", repr(r_exc["shrunk"]),
          "异常:", type(r_exc["error"]).__name__, "-", r_exc["error"])
    assert r_exc["shrunk"] == 4

    print("\n全部演示断言通过。")


if __name__ == "__main__":
    main()
