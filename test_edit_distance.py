"""edit_distance 库的自测，运行：python3 test_edit_distance.py [-v]"""

import random
import time
import unittest

from edit_distance import Costs, Match, distance, find_similar, similarity


class TestDistanceBasics(unittest.TestCase):
    def test_empty_strings(self):
        self.assertEqual(distance("", "")[0], 0)
        self.assertEqual(distance("", "abc")[0], 3)   # 3 次插入
        self.assertEqual(distance("abc", "")[0], 3)   # 3 次删除

    def test_identical(self):
        self.assertEqual(distance("hello world", "hello world")[0], 0)

    def test_single_operations(self):
        self.assertEqual(distance("abc", "abcd")[0], 1)   # 插入
        self.assertEqual(distance("abcd", "abc")[0], 1)   # 删除
        self.assertEqual(distance("abc", "axc")[0], 1)    # 替换

    def test_single_adjacent_transposition(self):
        # 只差一次相邻交换：代价为 transpose（默认 1），而非两次替换
        self.assertEqual(distance("ab", "ba")[0], 1)
        self.assertEqual(distance("ca", "ac")[0], 1)
        self.assertEqual(distance("hte", "the")[0], 1)
        self.assertEqual(distance("recieve", "receive")[0], 1)

    def test_classic_examples(self):
        self.assertEqual(distance("kitten", "sitting")[0], 3)
        self.assertEqual(distance("flaw", "lawn")[0], 2)

    def test_custom_costs(self):
        c = Costs(insert=2, delete=3, substitute=1, transpose=1)
        self.assertEqual(distance("", "a", c)[0], 2)        # 插入代价 2
        self.assertEqual(distance("a", "", c)[0], 3)        # 删除代价 3
        self.assertEqual(distance("ab", "ba", c)[0], 1)     # 交换仍最便宜
        # 替换代价高于 删+插 时，应走 删+插
        c2 = Costs(insert=1, delete=1, substitute=5, transpose=9)
        self.assertEqual(distance("a", "b", c2)[0], 2)

    def test_invalid_costs(self):
        with self.assertRaises(ValueError):
            Costs(insert=-1)


class TestThreshold(unittest.TestCase):
    def test_threshold_not_exceeded_matches_full(self):
        rng = random.Random(42)
        alphabet = "abcd"
        for _ in range(200):
            a = "".join(rng.choices(alphabet, k=rng.randint(0, 30)))
            b = "".join(rng.choices(alphabet, k=rng.randint(0, 30)))
            full, _ = distance(a, b)
            for k in (full, full + 3):
                got, _ = distance(a, b, threshold=k)
                self.assertEqual(got, full, f"a={a!r} b={b!r} k={k}")

    def test_threshold_exceeded_returns_none(self):
        self.assertEqual(distance("kitten", "sitting", threshold=2)[0], None)
        self.assertEqual(distance("", "abc", threshold=2)[0], None)
        self.assertEqual(distance("abc", "", threshold=2)[0], None)
        self.assertEqual(distance("aaaa", "bbbb", threshold=3)[0], None)

    def test_threshold_boundary(self):
        # 距离恰等于阈值时必须返回真实值
        self.assertEqual(distance("kitten", "sitting", threshold=3)[0], 3)
        self.assertEqual(distance("ab", "ba", threshold=1)[0], 1)

    def test_early_termination_computes_fewer_cells(self):
        rng = random.Random(7)
        a = "".join(rng.choices("acgt", k=2000))
        b = "".join(rng.choices("acgt", k=2000))
        _, full_cells = distance(a, b)
        _, band_cells = distance(a, b, threshold=100)
        self.assertLess(band_cells, full_cells // 3)


class TestSimilarity(unittest.TestCase):
    def test_similarity(self):
        self.assertEqual(similarity("", ""), 1.0)
        self.assertEqual(similarity("abc", "abc"), 1.0)
        self.assertAlmostEqual(similarity("abc", "abd"), 1 - 1 / 3)
        self.assertEqual(similarity("", "abc"), 0.0)


class TestFindSimilar(unittest.TestCase):
    def test_exact_occurrences(self):
        hits = find_similar("abcabcabc", "abc", max_distance=0)
        self.assertEqual([(h.start, h.end) for h in hits],
                         [(0, 3), (3, 6), (6, 9)])

    def test_fuzzy_hit_with_transposition(self):
        text = "xxtheyyhtez"
        hits = find_similar(text, "the", max_distance=1)
        spans = [(h.start, h.end, h.distance) for h in hits]
        self.assertIn((2, 5, 0), spans)   # 完全匹配 "the"
        self.assertIn((7, 10, 1), spans)  # "hte" 一次相邻交换

    def test_min_similarity_threshold(self):
        hits = find_similar("hello world", "world", min_similarity=1.0)
        self.assertEqual([(h.start, h.end) for h in hits], [(6, 11)])

    def test_no_threshold_raises(self):
        with self.assertRaises(ValueError):
            find_similar("abc", "a")

    def test_empty_pattern(self):
        self.assertEqual(find_similar("abc", "", max_distance=0),
                         [Match(0, 0, 0)])

    def test_empty_text(self):
        self.assertEqual(find_similar("", "abc", max_distance=2), [])
        hits = find_similar("", "abc", max_distance=3)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].distance, 3)

    def test_overlaps_deduplicated(self):
        hits = find_similar("aaaaaa", "aa", max_distance=0)
        self.assertEqual([(h.start, h.end) for h in hits],
                         [(0, 2), (2, 4), (4, 6)])

    def test_very_long_text(self):
        rng = random.Random(2026)
        n = 200_000
        text = list("".join(rng.choices("acgt", k=n)))
        pattern = "GATTACA".lower()
        # 在已知位置植入：1 个完全匹配、1 个替换、1 个相邻交换
        plants = [(10_000, "gattaca"), (100_000, "gattaga"),
                  (150_000, "gattaca")]
        for pos, frag in plants:
            text[pos:pos + len(frag)] = frag
        text[150_000 + 3], text[150_000 + 4] = "a", "t"  # gatatca
        text = "".join(text)

        t0 = time.perf_counter()
        hits = find_similar(text, pattern, max_distance=1)
        elapsed = time.perf_counter() - t0

        by_pos = {h.start: h for h in hits}
        self.assertIn(10_000, by_pos)
        self.assertEqual(by_pos[10_000].distance, 0)
        self.assertIn(100_000, by_pos)
        self.assertEqual(by_pos[100_000].distance, 1)
        self.assertIn(150_000, by_pos)
        self.assertEqual(by_pos[150_000].distance, 1)
        print(f"\n[long-text] n={n}, hits={len(hits)}, "
              f"elapsed={elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
