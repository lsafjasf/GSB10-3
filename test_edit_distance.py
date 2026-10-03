"""edit_distance 库的单元测试与边界用例。运行：python3 test_edit_distance.py"""

import random
import unittest

from edit_distance import (Costs, edit_distance, edit_distance_bounded,
                           find_similar)


class TestBasicDistance(unittest.TestCase):
    def test_empty_strings(self):
        self.assertEqual(edit_distance("", ""), 0)
        self.assertEqual(edit_distance("", "abc"), 3)
        self.assertEqual(edit_distance("abc", ""), 3)

    def test_identical(self):
        self.assertEqual(edit_distance("hello", "hello"), 0)
        self.assertEqual(edit_distance("a" * 500, "a" * 500), 0)

    def test_single_operations(self):
        self.assertEqual(edit_distance("abc", "abcd"), 1)   # 插入
        self.assertEqual(edit_distance("abcd", "abc"), 1)   # 删除
        self.assertEqual(edit_distance("abc", "axc"), 1)    # 替换

    def test_single_adjacent_transposition(self):
        # 只差一个相邻交换
        self.assertEqual(edit_distance("ab", "ba"), 1)
        self.assertEqual(edit_distance("abcd", "acbd"), 1)
        self.assertEqual(edit_distance("kitten", "kittne"), 1)

    def test_transposition_vs_two_substitutions(self):
        # 交换代价为 1，应优于两次替换（代价 2）
        self.assertEqual(edit_distance("ab", "ba"), 1)
        # 把交换代价调大后应退化为两次替换
        costly = Costs(transpose=5)
        self.assertEqual(edit_distance("ab", "ba", costly), 2)

    def test_configurable_costs(self):
        c = Costs(insert=2, delete=3, substitute=4, transpose=1)
        self.assertEqual(edit_distance("", "a", c), 2)          # 插入
        self.assertEqual(edit_distance("a", "", c), 3)          # 删除
        self.assertEqual(edit_distance("a", "b", c), 4)         # 替换
        self.assertEqual(edit_distance("a", "b", Costs(substitute=9)), 2)  # 删+插更便宜
        self.assertEqual(edit_distance("ab", "ba", c), 1)       # 交换

    def test_known_values(self):
        self.assertEqual(edit_distance("kitten", "sitting"), 3)
        self.assertEqual(edit_distance("flaw", "lawn"), 2)

    def test_bounded_matches_full(self):
        rng = random.Random(42)
        for _ in range(200):
            la, lb = rng.randint(0, 30), rng.randint(0, 30)
            a = "".join(rng.choice("abcd") for _ in range(la))
            b = "".join(rng.choice("abcd") for _ in range(lb))
            full = edit_distance(a, b)
            for t in (0, 1, 2, 5, 10, 60):
                r = edit_distance_bounded(a, b, t)
                if full <= t:
                    self.assertFalse(r.exceeded, (a, b, t))
                    self.assertEqual(r.distance, full, (a, b, t))
                else:
                    self.assertTrue(r.exceeded, (a, b, t))
                    self.assertIsNone(r.distance)

    def test_bounded_with_custom_costs(self):
        rng = random.Random(7)
        c = Costs(insert=2, delete=1, substitute=3, transpose=1)
        for _ in range(100):
            a = "".join(rng.choice("ab") for _ in range(rng.randint(0, 20)))
            b = "".join(rng.choice("ab") for _ in range(rng.randint(0, 20)))
            full = edit_distance(a, b, c)
            for t in (0, 3, 8, 40):
                r = edit_distance_bounded(a, b, t, c)
                self.assertEqual(r.exceeded, full > t, (a, b, t))
                if not r.exceeded:
                    self.assertEqual(r.distance, full)

    def test_bounded_edge_cases(self):
        self.assertEqual(edit_distance_bounded("", "", 0).distance, 0)
        self.assertTrue(edit_distance_bounded("", "abc", 2).exceeded)
        self.assertEqual(edit_distance_bounded("", "abc", 3).distance, 3)
        self.assertEqual(edit_distance_bounded("ab", "ba", 1).distance, 1)
        self.assertTrue(edit_distance_bounded("abc", "xyz", 2).exceeded)


class TestFindSimilar(unittest.TestCase):
    def test_exact_occurrences(self):
        hits = find_similar("abcabcabc", "abc", max_distance=0)
        self.assertEqual([(h.start, h.end) for h in hits], [(0, 3), (3, 6), (6, 9)])
        self.assertTrue(all(h.distance == 0 and h.similarity == 1.0 for h in hits))

    def test_fuzzy_hit_with_transposition(self):
        # 文本中 "acb" 与模式 "abc" 只差一次相邻交换
        hits = find_similar("xxacbyy", "abc", max_distance=1)
        # "ac"（删 1 字符）与 "acb"（1 次相邻交换）同为距离 1 的合法命中
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].start, 2)
        self.assertEqual(hits[0].distance, 1)

    def test_no_hit(self):
        self.assertEqual(find_similar("aaaa", "bbbb", max_distance=2), [])

    def test_empty_text_and_pattern(self):
        self.assertEqual(find_similar("", "abc", max_distance=3), [])
        with self.assertRaises(ValueError):
            find_similar("abc", "", max_distance=1)

    def test_min_similarity_threshold(self):
        hits = find_similar("xxabcyy", "abc", min_similarity=0.7)
        self.assertEqual(len(hits), 1)
        self.assertGreaterEqual(hits[0].similarity, 0.7)

    def test_planted_hits_in_long_text(self):
        rng = random.Random(1)
        text = list(rng.choice("ACGT") for _ in range(20000))
        pattern = "ACGTTGCATGTCGCATGATGCATG"
        planted = []
        for pos in (1000, 8000, 15000):
            mut = list(pattern)
            idx = rng.randrange(len(mut))
            mut[idx] = rng.choice("ACGT")          # 一次替换
            text[pos:pos + len(pattern)] = mut
            planted.append(pos)
        hits = find_similar("".join(text), pattern, max_distance=1)
        for pos in planted:
            self.assertTrue(any(abs(h.start - pos) <= 1 for h in hits),
                            f"未命中植入位置 {pos}")

    def test_very_long_text_smoke(self):
        # 超长文本冒烟测试：10 万字符
        rng = random.Random(2)
        text = "".join(rng.choice("ACGT") for _ in range(100_000))
        pattern = "ACGTTGCATGTC"
        pos = 54321
        text = text[:pos] + pattern + text[pos + len(pattern):]
        hits = find_similar(text, pattern, max_distance=0)
        self.assertTrue(any(h.start == pos for h in hits))


if __name__ == "__main__":
    unittest.main(verbosity=2)
