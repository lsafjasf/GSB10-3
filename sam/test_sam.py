"""后缀自动机自测 + 与暴力算法对拍。标准库 unittest，无需第三方依赖。"""

import os
import random
import time
import unittest
from collections import Counter

from sam import SuffixAutomaton


# ------------------------------------------------------------- brute force
def brute_distinct_substrings(text: str) -> int:
    return len({text[i:j] for i in range(len(text)) for j in range(i + 1, len(text) + 1)})


def brute_count(text: str, pattern: str) -> int:
    if pattern == "":
        return len(text) + 1
    return sum(1 for i in range(len(text) - len(pattern) + 1) if text.startswith(pattern, i))


def brute_max_occurrence(text: str):
    """任意非空子串的最大出现次数，以及达到该值的一个子串。"""
    counts = Counter()
    for i in range(len(text)):
        for j in range(i + 1, len(text) + 1):
            counts[text[i:j]] += 1
    if not counts:
        return 0, ""
    sub, cnt = counts.most_common(1)[0]
    return cnt, sub


def brute_lcs(a: str, b: str) -> str:
    best = ""
    for i in range(len(a)):
        for j in range(i + 1, len(a) + 1):
            if j - i > len(best) and a[i:j] in b:
                best = a[i:j]
    return best


def random_text(n: int, alphabet: str, seed: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(alphabet) for _ in range(n))


# ------------------------------------------------------------------- tests
class TestEdgeCases(unittest.TestCase):
    def test_empty_text(self):
        sam = SuffixAutomaton("")
        self.assertEqual(sam.node_count, 1)
        self.assertEqual(sam.num_distinct_substrings(), 0)
        self.assertFalse(sam.contains("a"))
        self.assertEqual(sam.count_occurrences("a"), 0)
        self.assertTrue(sam.contains(""))
        self.assertEqual(sam.longest_common_substring("abc"), "")

    def test_single_char(self):
        sam = SuffixAutomaton("a")
        self.assertEqual(sam.node_count, 2)
        self.assertEqual(sam.num_distinct_substrings(), 1)
        self.assertTrue(sam.contains("a"))
        self.assertFalse(sam.contains("b"))
        self.assertEqual(sam.count_occurrences("a"), 1)
        self.assertEqual(sam.longest_common_substring("xa"), "a")
        self.assertEqual(sam.longest_common_substring("yy"), "")

    def test_unichar_texts(self):
        # 单字符与全部相同：节点数恒为 n+1（无克隆），不同子串恰为 n
        for n in (2, 3, 10, 100):
            text = "a" * n
            sam = SuffixAutomaton(text)
            self.assertEqual(sam.node_count, n + 1)
            self.assertEqual(sam.clones, 0)
            self.assertEqual(sam.num_distinct_substrings(), n)
            for k in range(1, n + 1):
                pat = "a" * k
                self.assertEqual(sam.count_occurrences(pat), n - k + 1)
            self.assertEqual(sam.count_occurrences("b"), 0)

    def test_pattern_queries(self):
        sam = SuffixAutomaton("abcabcabc")
        for pat, want in (("abc", 3), ("bc", 3), ("c", 3), ("abcabc", 2), ("", 10)):
            self.assertEqual(sam.count_occurrences(pat), want, pat)
        self.assertFalse(sam.contains("abd"))
        self.assertFalse(sam.contains("abcabcd"))
        self.assertTrue(sam.contains("abcabcabc"))

    def test_lcs(self):
        a = SuffixAutomaton("abcabcabc")
        self.assertEqual(a.longest_common_substring("xabcabcy"), "abcabc")
        self.assertEqual(
            a.longest_common_substring("zzzcabczzz"),
            brute_lcs("abcabcabc", "zzzcabczzz"),
        )

    def test_rebuild_resets(self):
        sam = SuffixAutomaton("aaaa")
        sam.build("ababa")
        self.assertEqual(sam.node_count, len(sam.states))
        self.assertEqual(sam.count_occurrences("aba"), 2)
        self.assertEqual(sam.num_distinct_substrings(), brute_distinct_substrings("ababa"))


class TestBruteComparison(unittest.TestCase):
    CASES = [
        ("single", "a"),
        ("two_same", "aa"),
        ("all_same_50", "a" * 50),
        ("abab_repeat", "ab" * 40),
        ("periodic", ("abcde" * 20)),
        ("random_binary", random_text(300, "ab", seed=1)),
        ("random_small", random_text(200, "abc", seed=2)),
        ("random_dna", random_text(300, "acgt", seed=3)),
        ("random_large_alpha", random_text(300, "abcdefghij", seed=4)),
        ("repeated_block", "xyzxyz" * 30 + "tail"),
        ("pattern_in_noise", "aaab" * 10 + random_text(100, "ab", seed=5)),
    ]

    def test_distinct_count_matches_brute(self):
        for name, text in self.CASES:
            sam = SuffixAutomaton(text)
            self.assertEqual(
                sam.num_distinct_substrings(),
                brute_distinct_substrings(text),
                name,
            )

    def test_occurrences_match_brute(self):
        rng = random.Random(42)
        for name, text in self.CASES:
            sam = SuffixAutomaton(text)
            candidates = {
                text[i:j]
                for i in range(min(len(text), 40))
                for j in range(i + 1, min(len(text) + 1, i + 12))
            }
            for _ in range(60):
                k = rng.randint(1, min(8, len(text) or 1))
                start = rng.randrange(len(text) - k + 1) if text else 0
                candidates.add(text[start:start + k])
            candidates.update(["z", "zz", "q0", "ababab", "xyzxyzxyz"])
            for pat in candidates:
                self.assertEqual(
                    sam.count_occurrences(pat),
                    brute_count(text, pat),
                    f"{name}: {pat!r}",
                )
            # 最大出现次数（最优子串恰为最长单字符游程，用 SAM 状态 occ 求 max）
            sam_max = max((st.occ for st in sam.states[1:]), default=0)
            brute_max, brute_pat = brute_max_occurrence(text)
            self.assertEqual(sam_max, brute_max, name)
            self.assertEqual(sam.count_occurrences(brute_pat), brute_max, name)

    def test_lcs_matches_brute(self):
        pairs = [
            ("abcabcabc", "xabcabcy"),
            ("aaaa", "aa"),
            ("ab" * 40, "baba"),
            (random_text(120, "abc", 11), random_text(120, "abc", 12)),
            (random_text(100, "acgt", 13), random_text(80, "acgt", 14)),
            ("", "abc"),
            ("abc", ""),
        ]
        for a, b in pairs:
            sam = SuffixAutomaton(a)
            got = sam.longest_common_substring(b)
            want = brute_lcs(a, b)
            self.assertEqual(len(got), len(want), (a, b))
            self.assertIn(got, a)
            self.assertIn(got, b)


class TestRandomStress(unittest.TestCase):
    def test_randomized_small(self):
        rng = random.Random(100)
        for trial in range(200):
            n = rng.randint(0, 25)
            alpha = "ab" if trial % 2 else "abc"
            text = "".join(rng.choice(alpha) for _ in range(n))
            sam = SuffixAutomaton(text)
            self.assertEqual(sam.num_distinct_substrings(), brute_distinct_substrings(text))
            sam_max = max((st.occ for st in sam.states[1:]), default=0)
            brute_max, _ = brute_max_occurrence(text)
            self.assertEqual(sam_max, brute_max)
            for _ in range(10):
                if n == 0:
                    break
                k = rng.randint(1, max(1, n))
                start = rng.randrange(n - k + 1)
                pat = text[start:start + k]
                self.assertEqual(sam.count_occurrences(pat), brute_count(text, pat))


# ----------------------------------------------------- scale/data reporting
def print_node_scale_table():
    print("== 节点规模数据（n = 文本长度） ==")
    print(f"{'case':<22}{'n':>7}{'nodes':>7}{'2n+1':>7}{'nodes/n':>9}{'clones':>8}")
    cases = [
        ("all_same", lambda n: "a" * n),
        ("binary_random", lambda n: random_text(n, "ab", 7)),
        ("dna_random", lambda n: random_text(n, "acgt", 8)),
        ("alpha10_random", lambda n: random_text(n, "abcdefghij", 9)),
        ("periodic_ab", lambda n: ("ab" * ((n + 1) // 2))[:n]),
        ("periodic_block", lambda n: (("abcdefgh" * ((n // 8) + 1))[:n])),
    ]
    for n in (100, 1000, 10000, 100000):
        for name, fn in cases:
            t0 = time.perf_counter()
            sam = SuffixAutomaton(fn(n))
            dt = time.perf_counter() - t0
            assert sam.node_count <= 2 * n + 1
            print(
                f"{name:<22}{n:>7}{sam.node_count:>7}{2 * n + 1:>7}"
                f"{sam.node_count / n:>9.3f}{sam.clones:>8}  build={dt:.3f}s"
            )
    print("理论上界: nodes <= 2n (n>=2), n=1 时 nodes=2; 本实现 nodes <= 2n-1 (n>=2)")


def print_crosscheck_data():
    print("\n== 对拍数据（SAM vs 暴力） ==")
    print(f"{'case':<20}{'n':>6}{'distinct':>10}{'brute':>10}{'maxOcc':>8}{'bruteMax':>10}")
    cases = [
        ("single", "a"),
        ("all_same", "a" * 200),
        ("ab_repeat", "ab" * 100),
        ("block_repeat", "xyz" * 80),
        ("random_binary", random_text(250, "ab", 21)),
        ("random_dna", random_text(250, "acgt", 22)),
        ("random_alpha", random_text(250, "abcdefgh", 23)),
    ]
    for name, text in cases:
        sam = SuffixAutomaton(text)
        d1 = sam.num_distinct_substrings()
        d2 = brute_distinct_substrings(text)
        m1 = max((st.occ for st in sam.states[1:]), default=0)
        m2, _ = brute_max_occurrence(text)
        assert d1 == d2 and m1 == m2
        print(f"{name:<20}{len(text):>6}{d1:>10}{d2:>10}{m1:>8}{m2:>10}")


if __name__ == "__main__":
    if os.environ.get("SAM_BENCH"):
        print_node_scale_table()
        print_crosscheck_data()
    else:
        unittest.main(verbosity=2)
