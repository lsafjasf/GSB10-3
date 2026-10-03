"""
test_regex_automata.py — 自测（仅标准库，无外部框架）

运行：
    python3 test_regex_automata.py

内容：
  1. 等价判定结果表（equivalent 的返回值与预期逐行对照）
  2. 语言包含断言：最小化前后 L(min) ⊆ L(raw) 且 L(raw) ⊆ L(min)，
     用两种独立手段验证：
       (a) 乘积自动机判空（精确，自动机层面）
       (b) 有界穷举 alphabet^{≤5} 逐串比对（有限范围的独立交叉验证，
           非随机采样，判定结论本身只依赖 (a) 与 canonical 同一化）
  3. 边界用例：空语言、只接受空串、闭包嵌套、∅/ε 传播、字符类、
     必须先最小化才能看出等价的表达式。
"""

import sys
import traceback

from regex_automata import (
    compile_dfa,
    canonical,
    enumerate_strings,
    equivalent,
    is_subset,
    parse,
    ast_alphabet,
)

PASS = "通过"
FAIL = "失败"

# (规则 A, 规则 B, 预期等价, 说明)
EQUIV_CASES = [
    ("∅", "∅", True, "两个空语言"),
    ("∅", "a∅", True, "含 ∅ 的连接仍是空语言"),
    ("∅", "∅*", False, "∅* = ε，不等于 ∅"),
    ("ε", "∅*", True, "空语言的闭包只接受空串"),
    ("ε", "εε", True, "ε 连接 ε"),
    ("ε", "ε*", True, "ε 的闭包"),
    ("a|∅", "a", True, "并上 ∅ 不变"),
    ("a*", "(a*)*", True, "嵌套闭包消去"),
    ("(a|b)*", "(a*b*)*", True, "需要最小化才能同一化的经典对"),
    ("(ab)*a", "a(ba)*", True, "左右移位，结构不同语言相同"),
    ("a+", "aa*", True, "+ 语法糖等价"),
    ("ab?", "a|ab", True, "? 与并展开"),
    ("[a-c]", "a|b|c", True, "字符类区间"),
    ("[abc]", "[a-bc]", True, "字符类两种写法"),
    ("[a-c]*", "(a|b|c)*", True, "字符类 + 闭包"),
    ("a*", "a+", False, "区别在空串"),
    ("(a|b)*", "(ab)*", False, "区别在 a 等单字符串"),
    ("a|b", "a", False, "真超集"),
    ("a", "b", False, "不同单字符"),
    ("ε", "a*", False, "只接受空串 vs 接受所有 a^n"),
    ("(a|ε)(a|ε)", "a{?}".replace("{?}", ""), True, "占位（下方替换）"),
]
# 最后一行用简单等价对替换，避免奇怪写法
EQUIV_CASES[-1] = ("(a|ε)(a|ε)", "ε|a|aa", True, "ε 参与的连接展开")


def run_equivalence_table():
    print("=" * 78)
    print("等价判定结果表（canonical form 同一化判定，非采样）")
    print("=" * 78)
    print(f"{'规则 A':<16}{'规则 B':<16}{'判定':<8}{'预期':<8}{'结果'}")
    print("-" * 78)
    ok_all = True
    for pa, pb, expect, note in EQUIV_CASES:
        got = equivalent(pa, pb)
        ok = got == expect
        ok_all &= ok
        print(f"{pa:<16}{pb:<16}{'等价' if got else '不等':<8}"
              f"{'等价' if expect else '不等':<8}{PASS if ok else FAIL}  {note}")
    print("-" * 78)
    print("小结:", "全部符合预期" if ok_all else "存在不符合预期的判定！")
    return ok_all


# 需要做「最小化前后双向包含」断言的规则
LANGUAGE_CASES = [
    "∅",
    "ε",
    "∅|ε",
    "a*",
    "(a|b)*abb",
    "a+",
    "(ab)*a",
    "(a*b*)*",
    "[a-c]+",
    "ε|(aa)*",
    "∅*",
    "(a|ε)b*",
]

ENUM_MAXLEN = 5


def run_inclusion_assertions():
    print()
    print("=" * 78)
    print("语言包含断言：L(raw) ⊆ L(min) 且 L(min) ⊆ L(raw)")
    print("=" * 78)
    print(f"{'规则':<16}{'raw态':<7}{'min态':<7}"
          f"{'乘积判空':<12}{f'穷举≤{ENUM_MAXLEN}':<12}结果")
    print("-" * 78)
    ok_all = True
    for pat in LANGUAGE_CASES:
        raw, mini = compile_dfa(pat)
        # (a) 精确：乘积自动机判空
        fwd = is_subset(raw, mini)     # L(raw) ⊆ L(min)
        bwd = is_subset(mini, raw)     # L(min) ⊆ L(raw)
        product_ok = fwd and bwd
        assert fwd, f"[乘积断言失败] L(raw) 不包含于 L(min): {pat!r}"
        assert bwd, f"[乘积断言失败] L(min) 不包含于 L(raw): {pat!r}"

        # (b) 独立交叉验证：有界穷举逐串比对
        alpha = raw.alphabet
        enum_ok = all(
            raw.accepts_str(w) == mini.accepts_str(w)
            for w in enumerate_strings(alpha, ENUM_MAXLEN)
        )
        assert enum_ok, f"[穷举断言失败] 最小化前后接受不一致: {pat!r}"

        ok_all &= product_ok and enum_ok
        print(f"{pat:<16}{raw.num_states():<7}{mini.num_states():<7}"
              f"{'双向包含':<12}{'一致':<12}{PASS}")
    print("-" * 78)
    print("小结:", "所有双向包含断言成立" if ok_all else "断言失败！")
    return ok_all


def run_boundary_specific():
    print()
    print("=" * 78)
    print("边界用例专项")
    print("-" * 78)
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    raw0, min0 = compile_dfa("∅", alphabet=frozenset("a"))
    check("∅ 不接受 ε", not min0.accepts_str(""))
    check("∅ 不接受任意串", not min0.accepts_str("a"))
    check("∅ 最小化后无终态", len(min0.accepts) == 0)

    rawe, mine = compile_dfa("ε", alphabet=frozenset("a"))
    check("ε 接受空串", mine.accepts_str(""))
    check("ε 不接受非空串", not mine.accepts_str("a"))

    star, mstar = compile_dfa("∅*", alphabet=frozenset("a"))
    check("∅* ≡ ε（接受空串）", mstar.accepts_str(""))
    check("∅* 不接受 a", not mstar.accepts_str("a"))

    astar, mastar = compile_dfa("a*", alphabet=frozenset("ab"))
    check("a* 接受 aaa", mastar.accepts_str("aaa"))
    check("a* 拒绝 b", not mastar.accepts_str("b"))

    # 等价但状态数不同的对：最小化后 canonical form 必须一致
    r1, m1 = compile_dfa("(a|b)*abb")
    r2, m2 = compile_dfa("(a|b)*abb|∅")
    check("冗余写法最小化后状态数不增", m2.num_states() <= r2.num_states())
    check("canonical 同构一致", canonical(m1) == canonical(m2))

    # 最小化确实让非最简自动机变小（证明判定前化简是必要的）
    check("(a|b)*abb 原始 DFA 状态数 > 最小 DFA",
          r1.num_states() > m1.num_states())
    raw_p, min_p = compile_dfa("a*")
    check("a* 原始 DFA 2 态, 最小化收敛到 1 态",
          raw_p.num_states() == 2 and min_p.num_states() == 1)

    # 转义特殊字符
    check("转义 * 按字面量处理",
          equivalent("a\\*b", "a\\*b"))
    check("字面 * 不等于 a*",
          not equivalent("a\\*", "a*"))

    ok_all = True
    for name, ok in checks:
        ok_all &= ok
        print(f"  [{PASS if ok else FAIL}] {name}")
    print("-" * 78)
    print("小结:", "全部边界用例通过" if ok_all else "边界用例失败！")
    return ok_all


def main():
    results = []
    for fn in (run_equivalence_table, run_inclusion_assertions,
               run_boundary_specific):
        try:
            results.append(fn())
        except AssertionError:
            traceback.print_exc()
            results.append(False)
    print()
    print("=" * 78)
    print("总体:", "全部自测通过 ✅" if all(results) else "存在失败 ❌")
    print("=" * 78)
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
