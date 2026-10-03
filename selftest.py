"""自测：覆盖完全相同 / 仅标点不同 / 词序颠倒 / 完全不同 / 变格 / 复用决策。

运行：python3 selftest.py   （或 python3 -m unittest selftest -v）
"""

import unittest

from fuzzy_tm import (TranslationMemory, TMEntry, diff_segments,
                      render_diff_plain, similarity, similarity_overlap,
                      tokenize)


class TestSimilarity(unittest.TestCase):
    def test_identical(self):
        s = similarity("The file was saved successfully.",
                       "The file was saved successfully.")
        self.assertEqual(s.score, 1.0)

    def test_punctuation_only(self):
        s = similarity("The file was saved successfully.",
                       "The file was saved successfully!")
        self.assertGreaterEqual(s.score, 0.9)
        self.assertLess(s.score, 1.0)

    def test_word_order_reversed_penalized(self):
        ours = similarity("The cat chased the dog.",
                          "The dog chased the cat.").score
        base = similarity_overlap("The cat chased the dog.",
                                  "The dog chased the cat.")
        self.assertEqual(base, 1.0)
        self.assertLess(ours, 0.9)
        self.assertGreater(ours, 0.5)

    def test_completely_different(self):
        s = similarity("The quick brown fox jumps over the lazy dog.",
                       "Quantum chromodynamics describes strong interactions.")
        self.assertLess(s.score, 0.3)

    def test_inflection_tolerated(self):
        s = similarity("All items were updated.", "All item were updated.")
        self.assertGreaterEqual(s.score, 0.9)
        s2 = similarity("The user is running the task.",
                        "The user is run the task.")
        self.assertGreaterEqual(s2.score, 0.9)

    def test_keyword_swap_scores_below_identical(self):
        swap = similarity("The file was saved successfully.",
                          "The file was deleted successfully.").score
        same = similarity("The file was saved successfully.",
                          "The file was saved successfully!").score
        self.assertLess(swap, same)

    def test_empty(self):
        self.assertEqual(similarity("", "").score, 1.0)
        self.assertLess(similarity("", "hello.").score, 0.5)

    def test_chinese(self):
        punct = similarity("把文件保存到本地磁盘。", "把文件保存到本地磁盘").score
        self.assertGreaterEqual(punct, 0.9)
        diff = similarity("把文件保存到本地磁盘。", "从本地磁盘删除文件。").score
        self.assertLess(diff, 0.7)

    def test_score_range(self):
        for a, b in [("a b c.", "c b a."), ("x", "y"), ("same", "same")]:
            score = similarity(a, b).score
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)


class TestDiff(unittest.TestCase):
    def test_replace_highlight(self):
        d = render_diff_plain("The file was deleted successfully.",
                              "The file was saved successfully.")
        self.assertIn("[-saved-]", d)
        self.assertIn("{+deleted+}", d)

    def test_punctuation_highlight(self):
        d = render_diff_plain("The file was saved successfully!",
                              "The file was saved successfully.")
        self.assertIn("[-.-]", d)
        self.assertIn("{+!+}", d)

    def test_morph_marked(self):
        d = render_diff_plain("All item were updated.",
                              "All items were updated.")
        self.assertIn("~items/item~", d)

    def test_insert_delete(self):
        d = render_diff_plain("Please restart the app now.",
                              "Please restart the app.")
        self.assertIn("{+now+}", d)
        d2 = render_diff_plain("Please restart the app.",
                               "Please restart the app now.")
        self.assertIn("[-now-]", d2)

    def test_identical_no_marks(self):
        d = render_diff_plain("把文件保存到本地磁盘。", "把文件保存到本地磁盘。")
        self.assertNotIn("[-", d)
        self.assertNotIn("{+", d)
        self.assertNotIn("~", d)

    def test_segments_cover_all_tokens(self):
        q, c = "The dog chased the cat.", "The cat chased the dog."
        segs = diff_segments(q, c)
        self.assertTrue(any(sg.kind != "equal" for sg in segs))


class TestReuse(unittest.TestCase):
    def setUp(self):
        self.tm = TranslationMemory([
            TMEntry("The file was saved successfully.", "文件保存成功。",
                    "tm://en2zh/0001"),
            TMEntry("The cat chased the dog.", "猫追狗。", "tm://en2zh/0002"),
        ])

    def test_auto_reuse_with_source(self):
        r = self.tm.lookup("The file was saved successfully!")
        self.assertTrue(r["reused"])
        self.assertEqual(r["reuse_target"], "文件保存成功。")
        self.assertEqual(r["source"]["sid"], "tm://en2zh/0001")
        self.assertIn("similarity", r)
        self.assertIn("diff", r)

    def test_below_threshold_not_reused(self):
        r = self.tm.lookup("The dog chased the cat.")
        self.assertFalse(r["reused"])
        self.assertNotIn("reuse_target", r)
        self.assertIn("人工", r["decision"])

    def test_far_below_threshold_not_reused(self):
        r = self.tm.lookup("Completely unrelated content here.")
        self.assertFalse(r["reused"])
        self.assertNotIn("reuse_target", r)

    def test_threshold_boundary(self):
        tm = TranslationMemory([TMEntry("a b c.", "x", "s1")], threshold=1.0)
        self.assertTrue(tm.lookup("a b c.")["reused"])
        self.assertFalse(tm.lookup("a b c!")["reused"])

    def test_empty_memory(self):
        tm = TranslationMemory([])
        r = tm.lookup("anything.")
        self.assertFalse(r["reused"])
        self.assertNotIn("source", r)


class TestTokenizer(unittest.TestCase):
    def test_stemming(self):
        norms = [t.norm for t in tokenize("items updated running saves")]
        self.assertEqual(norms, ["item", "updat", "run", "sav"])
        norms2 = [t.norm for t in tokenize("item update run save")]
        self.assertEqual(norms, norms2)

    def test_cjk_char_tokens(self):
        toks = tokenize("保存文件。")
        self.assertEqual([t.surface for t in toks],
                         ["保", "存", "文", "件", "。"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
