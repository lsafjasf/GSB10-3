import unittest

from punctnorm import normalize_width, pair


class TestEdge(unittest.TestCase):
    def test_empty_and_plain(self):
        self.assertTrue(pair("").ok)
        self.assertTrue(pair("没有任何标点").ok)
        self.assertEqual(normalize_width(""), "")

    def test_only_symbols(self):
        r = pair(")))")
        self.assertEqual(len(r.issues), 3)
        self.assertEqual(r.text, "")

    def test_unicode_indices_are_code_points(self):
        # 位置按码点计算，不受多字节影响
        r = pair("中文([)")
        pos = next(i.pos for i in r.issues if i.kind == "crossing")
        self.assertEqual("中文([)"[pos], ")")

    def test_nested_same_after_fix_balances(self):
        # 修复后再跑一次配对应当无冲突
        s = "（甲[乙{丙"
        fixed = pair(s).text
        self.assertTrue(pair(fixed).ok)

    def test_mixed_punct_sentence(self):
        s = "价格３．５元,重５ｋｇ(约１１斤)"
        out = normalize_width(s)
        self.assertEqual(out, "价格3.5元，重5kg（约11斤）")


if __name__ == "__main__":
    unittest.main()
