import unittest

from punctnorm import pair


class TestPair(unittest.TestCase):
    def test_normal_nested(self):
        r = pair("（甲【乙】丙）")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "（甲【乙】丙）")
        self.assertEqual(r.max_depth, 2)

    def test_normal_quotes_and_books(self):
        for s in ["他说「你好」", "《书名》", "“引文”", "a( b[ c{ d } ] )"]:
            self.assertTrue(pair(s).ok, s)

    def test_deep_nesting(self):
        s = "(" * 5 + "核" + ")" * 5
        r = pair(s)
        self.assertTrue(r.ok)
        self.assertEqual(r.max_depth, 5)
        self.assertEqual(r.text, s)

    def test_crossing_brackets(self):
        r = pair("([)]")
        kinds = [i.kind for i in r.issues]
        self.assertIn("crossing", kinds)
        iss = next(i for i in r.issues if i.kind == "crossing")
        self.assertEqual(iss.pos, 2)                       # ')' 在第 3 字符
        self.assertEqual(iss.related, [("[", 1), ("(", 0)])  # 交叉方与目标开符号
        self.assertEqual(r.text, "([])")                   # 先补 ] 再闭合 )

    def test_crossing_with_inner_text(self):
        r = pair("a([b)]c")
        self.assertEqual(any(i.kind == "crossing" for i in r.issues), True)
        self.assertEqual(r.text, "a([b])c")

    def test_crossing_report_mode_keeps_text(self):
        r = pair("([)]", mode="report")
        self.assertEqual(r.text, "([)]")
        self.assertTrue(any(i.kind == "crossing" for i in r.issues))

    def test_unclosed(self):
        r = pair("a[b", mode="fix")
        self.assertEqual(r.issues[0].kind, "unclosed")
        self.assertEqual(r.text, "a[b]")

    def test_unclosed_multilevel(self):
        r = pair("（甲[乙")
        self.assertEqual([i.kind for i in r.issues], ["unclosed", "unclosed"])
        self.assertEqual(r.text, "（甲[乙]）")

    def test_unclosed_mark_mode(self):
        r = pair("a[b", mode="mark")
        self.assertEqual(r.text, "a[b⟦+]⟧")

    def test_unmatched_close(self):
        r = pair("a]b", mode="fix")
        self.assertEqual(r.issues[0].kind, "unmatched_close")
        self.assertEqual(r.text, "ab")

    def test_unmatched_close_mark_mode(self):
        r = pair("a]b", mode="mark")
        self.assertEqual(r.text, "a⟦-]⟧b")

    def test_symmetric_quotes(self):
        r = pair('他说"你好"，走了')
        self.assertTrue(r.ok)
        r2 = pair('只有前引"没有后引')
        self.assertEqual(r2.issues[0].kind, "unclosed")
        self.assertEqual(r2.text, '只有前引"没有后引"')

    def test_quote_inside_brackets(self):
        r = pair('("引文")')
        self.assertTrue(r.ok)

    def test_full_half_not_cross_paired(self):
        # 全角开符号不能和半角闭符号配对：半角 ) 视为多余闭符号
        r = pair("（中文)")
        self.assertEqual([i.kind for i in r.issues],
                         ["unmatched_close", "unclosed"])

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            pair("x", mode="nope")


if __name__ == "__main__":
    unittest.main()
