import unittest

from punctnorm import diff_width, normalize, normalize_width


class TestConvert(unittest.TestCase):
    def test_decimal_number(self):
        self.assertEqual(normalize_width("３．５"), "3.5")

    def test_thousands_separator(self):
        self.assertEqual(normalize_width("１，０００"), "1,000")

    def test_time(self):
        self.assertEqual(normalize_width("１２：３０"), "12:30")

    def test_number_with_unit(self):
        self.assertEqual(normalize_width("重５ｋｇ"), "重5kg")
        self.assertEqual(normalize_width("容量3ＧＢ"), "容量3GB")

    def test_abbreviation(self):
        self.assertEqual(normalize_width("Ｕ．Ｓ．Ａ．"), "U.S.A.")

    def test_latin_word(self):
        self.assertEqual(normalize_width("他说Ｈｅｌｌｏ"), "他说Hello")

    def test_single_letter_between_cjk_kept(self):
        # 单个全角字母夹在中文之间，保留全角
        self.assertEqual(normalize_width("第Ａ章"), "第Ａ章")

    def test_half_punct_near_cjk(self):
        self.assertEqual(normalize_width("你好,世界"), "你好，世界")
        self.assertEqual(normalize_width("他走了."), "他走了。")
        self.assertEqual(normalize_width("真的吗?"), "真的吗？")

    def test_number_internal_dot_untouched(self):
        self.assertEqual(normalize_width("3.14 与 1,000"), "3.14 与 1,000")

    def test_full_punct_between_western(self):
        self.assertEqual(normalize_width("hello，world"), "hello,world")

    def test_brackets_context(self):
        self.assertEqual(normalize_width("中文(注)"), "中文（注）")
        self.assertEqual(normalize_width("foo(bar)"), "foo(bar)")

    def test_change_records_positions(self):
        changes = diff_width("３．５")
        self.assertEqual([(c.pos, c.after) for c in changes],
                         [(0, "3"), (1, "."), (2, "5")])
        self.assertTrue(all("数字" in c.reason for c in changes))

    def test_pipeline_then_pair_fix(self):
        r = normalize("价格３．５元,（甲【乙）")
        self.assertIn("3.5元，", r.text)       # 数字/标点已规范化
        self.assertTrue(any(i.kind == "crossing" for i in r.issues))
        self.assertTrue(r.width_changes)


if __name__ == "__main__":
    unittest.main()
