"""Self-tests + brute-force cross-checking. Run: python3 test_sam.py"""

import random
import unittest

from sam import SuffixAutomaton
import brute


class QueryTests(unittest.TestCase):
    def test_single_char(self):
        sam = SuffixAutomaton("a")
        self.assertEqual(sam.state_count, 2)
        self.assertTrue(sam.contains("a"))
        self.assertFalse(sam.contains("b"))
        self.assertFalse(sam.contains(""))
        self.assertEqual(sam.count_occurrences("a"), 1)
        self.assertEqual(sam.count_occurrences("b"), 0)
        self.assertEqual(sam.count_distinct_substrings(), 1)
        self.assertEqual(sam.max_occurrence(), 1)

    def test_all_equal(self):
        sam = SuffixAutomaton("aaaa")
        self.assertEqual(sam.state_count, 5)
        for k in range(1, 5):
            self.assertEqual(sam.count_occurrences("a" * k), 5 - k)
        self.assertEqual(sam.count_distinct_substrings(), 4)
        self.assertEqual(sam.max_occurrence(), 4)
        self.assertEqual(sam.longest_common_substring("xaaay"), (3, "aaa"))

    def test_repeated_pattern(self):
        text = "ababab"
        sam = SuffixAutomaton(text)
        self.assertEqual(sam.count_occurrences("ab"), 3)
        self.assertEqual(sam.count_occurrences("aba"), 2)
        self.assertEqual(sam.count_occurrences("bab"), 2)
        self.assertEqual(sam.count_occurrences("abab"), 2)
        self.assertEqual(sam.count_occurrences("ababab"), 1)
        self.assertEqual(sam.count_occurrences("bb"), 0)
        self.assertEqual(sam.count_distinct_substrings(), len(brute.distinct_substrings(text)))
        self.assertEqual(sam.longest_common_substring("babab"), (5, "babab"))

    def test_overlapping_counts(self):
        sam = SuffixAutomaton("aaaaa")
        self.assertEqual(sam.count_occurrences("aa"), 4)
        sam = SuffixAutomaton("abababa")
        self.assertEqual(sam.count_occurrences("aba"), 3)

    def test_empty_text(self):
        sam = SuffixAutomaton("")
        self.assertEqual(sam.state_count, 1)
        self.assertFalse(sam.contains("a"))
        self.assertEqual(sam.count_occurrences("a"), 0)
        self.assertEqual(sam.count_distinct_substrings(), 0)
        self.assertEqual(sam.max_occurrence(), 0)
        self.assertEqual(sam.longest_common_substring("abc"), (0, ""))
        self.assertEqual(SuffixAutomaton("abc").longest_common_substring(""), (0, ""))

    def test_unicode(self):
        sam = SuffixAutomaton("香蕉苹果香蕉")
        self.assertTrue(sam.contains("香蕉"))
        self.assertEqual(sam.count_occurrences("香蕉"), 2)
        self.assertEqual(sam.longest_common_substring("吃苹果"), (2, "苹果"))

    def test_lcs(self):
        sam = SuffixAutomaton("the quick brown fox")
        length, piece = sam.longest_common_substring("quick red foxes")
        self.assertEqual(length, len(piece))
        self.assertEqual(length, 6)  # "quick " or "quick"; tie broken arbitrarily
        self.assertIn(piece, "the quick brown fox")
        self.assertIn(piece, "quick red foxes")


class CrossCheckTests(unittest.TestCase):
    """Exhaustive brute-force cross-checking over many generated texts."""

    FIXED = [
        "",
        "a",
        "ab",
        "aa",
        "aaa",
        "aaaa",
        "abab",
        "ababab",
        "abcabcabc",
        "mississippi",
        "banana",
        "abracadabra",
        "aaaaaaaaaaaaaaaa",
        "0100101001001",
    ]

    def _assert_matches_brute(self, text):
        sam = SuffixAutomaton(text)
        expected = brute.occurrence_counts(text)

        self.assertEqual(sam.count_distinct_substrings(), len(expected), text)
        self.assertEqual(sam.max_occurrence(), max(expected.values(), default=0), text)

        for piece, times in expected.items():
            self.assertTrue(sam.contains(piece), (text, piece))
            self.assertEqual(sam.count_occurrences(piece), times, (text, piece))
        self.assertEqual(dict(sam.occurrence_table()), dict(expected), text)

        for absent in ("#", "zz", "abaabaaba"):
            if absent not in expected:
                self.assertFalse(sam.contains(absent), (text, absent))
                self.assertEqual(sam.count_occurrences(absent), 0)

        probes = [text[:k] for k in range(1, max(1, len(text) + 1))]
        for piece in probes:
            self.assertEqual(sam.count_occurrences(piece), brute.count_occurrences(text, piece))

        other = "xyz" + text[::2]
        length, piece = sam.longest_common_substring(other)
        expected = brute.longest_common_substring(text, other)
        self.assertEqual(length, len(piece), text)
        self.assertEqual(length, len(expected), (text, piece, expected))
        self.assertIn(piece, text, text)
        self.assertIn(piece, other, text)

    def test_fixed_texts(self):
        for text in self.FIXED:
            with self.subTest(text=text):
                self._assert_matches_brute(text)

    def test_random_small_alphabet(self):
        rng = random.Random(20240501)
        for _ in range(300):
            n = rng.randrange(0, 26)
            text = "".join(rng.choice("ab") for _ in range(n))
            self._assert_matches_brute(text)

    def test_random_medium_alphabet(self):
        rng = random.Random(20240502)
        alphabet = "abcde"
        for _ in range(120):
            n = rng.randrange(0, 60)
            text = "".join(rng.choice(alphabet) for _ in range(n))
            self._assert_matches_brute(text)

    def test_random_repeated_blocks(self):
        rng = random.Random(20240503)
        for _ in range(120):
            block = "".join(rng.choice("abc") for _ in range(rng.randrange(1, 5)))
            text = block * rng.randrange(1, 8)
            if rng.random() < 0.5:
                text += rng.choice("abc")
            self._assert_matches_brute(text)

    def test_state_bound(self):
        """Bound 1 <= states <= max(1, 2n-1), n = text length."""
        rng = random.Random(20240504)
        for _ in range(200):
            n = rng.randrange(0, 80)
            text = "".join(rng.choice("abcd") for _ in range(n))
            sam = SuffixAutomaton(text)
            upper = 1 if n == 0 else (2 if n == 1 else 2 * n - 1)
            self.assertGreaterEqual(sam.state_count, 1, (text, n))
            self.assertLessEqual(sam.state_count, upper, (text, n, sam.state_count))


if __name__ == "__main__":
    unittest.main(verbosity=2)
