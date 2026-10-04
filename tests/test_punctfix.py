# -*- coding: utf-8 -*-
"""punctfix 自测：正常配对 / 交叉嵌套 / 单边缺失 / 多层嵌套 / 宽度转换 / 边界。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from punctfix import (analyze_line, analyze_text, fix_line, mark_line,
                      normalize)
from punctfix.width import normalize_line as width_line
from punctfix.width import half_to_full_ascii


def kinds(analysis):
    return [i.kind for i in analysis.issues]


class TestNormalPairs(unittest.TestCase):
    """情形一：正常配对。"""

    def test_simple_brackets(self):
        la = analyze_line("（你好）【世界】")
        self.assertEqual(la.issues, [])
        self.assertEqual(len(la.pairs), 2)

    def test_mixed_quote_and_bracket(self):
        la = analyze_line("他说：“今天（周三）休息。”")
        self.assertEqual(la.issues, [])
        families = sorted(p.family for p in la.pairs)
        self.assertEqual(families, ["bracket", "quote"])

    def test_book_title_single_inside_double(self):
        la = analyze_line("《红楼梦〈卷一〉》")
        self.assertEqual(la.issues, [])

    def test_straight_double_quotes(self):
        la = analyze_line('say "hello" ok')
        self.assertEqual(la.issues, [])

    def test_ascii_brackets(self):
        la = analyze_line("f(a, [b, {c}])")
        self.assertEqual(la.issues, [])


class TestCrossNesting(unittest.TestCase):
    """情形二：交叉嵌套，必须指出冲突位置。"""

    def test_cross_detected_with_positions(self):
        text = "（甲 [ 乙 ） 丙 ]"
        la = analyze_line(text)
        self.assertEqual(kinds(la), ["cross"])
        iss = la.issues[0]
        self.assertEqual(iss.pos, text.index("）"))
        self.assertEqual(iss.blockers, [(text.index("["), "[")])
        self.assertIn("列", iss.detail)

    def test_cross_fix_is_balanced(self):
        la = analyze_line("（甲 [ 乙 ） 丙 ]")
        fixed = fix_line(la)
        again = analyze_line(fixed)
        self.assertEqual(again.issues, [])
        # 修复不丢字符：原文每个非括号字符都还在
        for ch in "甲乙丙":
            self.assertIn(ch, fixed)

    def test_cross_mark(self):
        la = analyze_line("（甲 [ 乙 ） 丙 ]")
        marked = mark_line(la)
        self.assertIn("⚠", marked)
        self.assertIn("⟦）⟧", marked)

    def test_double_cross(self):
        la = analyze_line("《a 〈 b 》 c 〉")
        self.assertEqual(kinds(la), ["cross"])
        fixed = fix_line(la)
        self.assertEqual(analyze_line(fixed).issues, [])

    def test_quote_cross(self):
        la = analyze_line("“甲 ‘乙 ” 丙 ’")
        self.assertEqual(kinds(la), ["cross"])
        fixed = fix_line(la)
        self.assertEqual(analyze_line(fixed).issues, [])


class TestUnpaired(unittest.TestCase):
    """情形三：单边缺失，fix 补齐 / mark 标注。"""

    def test_unpaired_open_fixed(self):
        la = analyze_line("（只有开")
        self.assertEqual(kinds(la), ["unpaired_open"])
        self.assertEqual(fix_line(la), "（只有开）")

    def test_unpaired_close_fixed(self):
        la = analyze_line("只有闭）")
        self.assertEqual(kinds(la), ["unpaired_close"])
        self.assertEqual(fix_line(la), "只有闭（）")

    def test_unpaired_quote_fixed(self):
        la = analyze_line("“缺一半")
        self.assertEqual(fix_line(la), "“缺一半”")

    def test_unpaired_mark(self):
        la = analyze_line("（只有开 和 闭））多了")
        marked = mark_line(la)
        self.assertEqual(marked, "（只有开 和 闭）⟦）⟧多了")

    def test_unpaired_open_mark(self):
        la = analyze_line("（只有开")
        self.assertEqual(mark_line(la), "⟦（⟧只有开")

    def test_fix_never_deletes(self):
        la = analyze_line("a）b（c")
        fixed = fix_line(la)
        self.assertEqual(analyze_line(fixed).issues, [])
        self.assertIn("a", fixed)
        self.assertIn("b", fixed)
        self.assertIn("c", fixed)


class TestDeepNesting(unittest.TestCase):
    """情形四：连续多层嵌套。"""

    def test_deep_nesting_ok(self):
        la = analyze_line("《一〈二「三『四』三」二〉一》")
        self.assertEqual(la.issues, [])
        self.assertEqual(len(la.pairs), 4)

    def test_deep_nesting_missing_innermost(self):
        la = analyze_line("（一（二（三）二）一")
        self.assertEqual(kinds(la), ["unpaired_open"])
        fixed = fix_line(la)
        self.assertEqual(fixed, "（一（二（三）二）一）")
        self.assertEqual(analyze_line(fixed).issues, [])

    def test_deep_nesting_with_cross_in_middle(self):
        la = analyze_line("（一 [ 二 { 三 ] 四 } 五 ）")
        self.assertEqual(kinds(la), ["cross"])
        fixed = fix_line(la)
        self.assertEqual(analyze_line(fixed).issues, [])


class TestWidthMismatch(unittest.TestCase):
    def test_mixed_width_pair_flagged_and_unified(self):
        la = analyze_line("（half)")
        self.assertEqual(kinds(la), ["width_mismatch"])
        self.assertEqual(fix_line(la), "（half）")

    def test_half_open_full_close(self):
        la = analyze_line("(x）")
        self.assertEqual(kinds(la), ["width_mismatch"])
        self.assertEqual(fix_line(la), "（x）")


class TestWidthConversion(unittest.TestCase):
    """全/半角转换：数字、单位、英文缩写等上下文。"""

    def w(self, s):
        return width_line(s)[0]

    def test_digits(self):
        self.assertEqual(self.w("共３００人"), "共300人")

    def test_decimal(self):
        self.assertEqual(self.w("圆周率３．１４"), "圆周率3.14")

    def test_thousands_and_time(self):
        self.assertEqual(self.w("１，０００ １２：３０"), "1,000 12:30")

    def test_percent(self):
        self.assertEqual(self.w("增长５０％"), "增长50%")

    def test_unit_spacing(self):
        self.assertEqual(self.w("重５ｋｇ"), "重5 kg")
        self.assertEqual(self.w("跑了１０ｋｍ"), "跑了10 km")

    def test_dotted_abbreviation(self):
        self.assertEqual(self.w("来自Ｕ．Ｓ．Ａ．"), "来自U.S.A.")

    def test_fullwidth_latin(self):
        self.assertEqual(self.w("Ａｐｐｌｅ公司"), "Apple公司")

    def test_cjk_punct_kept(self):
        self.assertEqual(self.w("你好，世界。真的！"), "你好，世界。真的！")

    def test_range_dash_kept_between_digits(self):
        self.assertEqual(self.w("１～１０"), "1～10")

    def test_fullwidth_space(self):
        self.assertEqual(self.w("甲　乙"), "甲 乙")

    def test_indent_kept(self):
        self.assertEqual(self.w("　　首行缩进"), "　　首行缩进")

    def test_ascii_context_punct(self):
        self.assertEqual(self.w("ｈｉ！ｔｈｅｒｅ"), "hi!there")

    def test_changes_recorded(self):
        _, changes = width_line("３．５")
        rules = {c.rule for c in changes}
        self.assertIn("digit", rules)
        self.assertIn("dot", rules)
        for c in changes:
            self.assertTrue(c.before or c.rule == "unit")

    def test_half_to_full_ascii(self):
        self.assertEqual(half_to_full_ascii("abc 123!"), "ａｂｃ　１２３！".replace("　", " "))
        self.assertEqual(half_to_full_ascii("a!"), "ａ！")


class TestEdgeCases(unittest.TestCase):
    """边界用例。"""

    def test_empty_string(self):
        r = normalize("")
        self.assertEqual(r.text, "")
        self.assertEqual(r.issues, [])

    def test_no_punctuation(self):
        r = normalize("纯文本没有标点")
        self.assertEqual(r.text, "纯文本没有标点")
        self.assertTrue(r.ok)

    def test_only_openers(self):
        la = analyze_line("（（（")
        self.assertEqual(kinds(la), ["unpaired_open"] * 3)
        self.assertEqual(fix_line(la), "（（（）））")

    def test_only_closers(self):
        la = analyze_line("））")
        self.assertEqual(kinds(la), ["unpaired_close"] * 2)

    def test_apostrophe_not_quote(self):
        la = analyze_line("it's a test")
        self.assertEqual(la.issues, [])

    def test_multiline_independent(self):
        r = normalize("（第一行没闭合\n第二行）")
        # 按行独立配对：第一行补 ），第二行的 ） 视为多余
        self.assertFalse(r.ok)
        self.assertEqual(len(r.issues), 2)

    def test_fix_then_reanalyze_clean(self):
        samples = [
            "（a [ b ） c ]",
            "“x ‘y ” z ’",
            "（（a）",
            "b）",
            "《甲〈乙》丙〉",
        ]
        for s in samples:
            fixed = normalize(s, strategy="fix").text
            again = analyze_text(fixed)
            leftover = [i for la in again for i in la.issues]
            self.assertEqual(leftover, [], f"{s!r} -> {fixed!r} 仍有问题")

    def test_mark_strategy_no_insert(self):
        r = normalize("（缺", strategy="mark")
        self.assertEqual(r.text, "⟦（⟧缺")

    def test_none_strategy(self):
        r = normalize("（缺", strategy="none")
        self.assertEqual(r.text, "（缺")
        self.assertFalse(r.ok)

    def test_bad_strategy(self):
        with self.assertRaises(ValueError):
            normalize("x", strategy="bogus")

    def test_pipeline_end_to_end(self):
        src = "价格是３．５０元（含税５％"
        r = normalize(src, strategy="fix")
        self.assertEqual(r.text, "价格是3.50元（含税5%）")
        self.assertEqual(len(r.issues), 1)
        self.assertEqual(r.issues[0].kind, "unpaired_open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
