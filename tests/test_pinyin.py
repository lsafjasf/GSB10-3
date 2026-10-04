# -*- coding: utf-8 -*-
"""拼音库自测：规则表对拍 + 单字/词组模式 + 多音字候选 + 边界用例。

运行：python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinyin_lib import (  # noqa: E402
    CHAR_TABLE, PHRASE_DICT, candidates, pinyin, segment, to_plain, to_tone_mark,
)

RULE_TABLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "rule_table.tsv",
)


class ToneRulesTest(unittest.TestCase):
    """声调标注规则：与手写规则用例逐项对拍（独立于数据表）。"""

    # (数字声调, 带声调, 不带声调)
    CASES = [
        ("zhong1", "zhōng", "zhong"),   # 有 a/o/e 标主元音
        ("hao3", "hǎo", "hao"),
        ("tou2", "tóu", "tou"),
        ("xue2", "xué", "xue"),
        ("bie2", "bié", "bie"),
        ("liu2", "liú", "liu"),         # iu 标第二个元音
        ("qiu1", "qiū", "qiu"),
        ("dui4", "duì", "dui"),         # ui 标第二个元音
        ("shui3", "shuǐ", "shui"),
        ("gui1", "guī", "gui"),
        ("zi5", "zi", "zi"),            # 轻声不标调
        ("de5", "de", "de"),
        ("lü4", "lǜ", "lü"),            # ü 直接标调
        ("nü3", "nǚ", "nü"),
        ("chuang2", "chuáng", "chuang"),
        ("kuai4", "kuài", "kuai"),
        ("ya1", "yā", "ya"),
        ("wu1", "wū", "wu"),
        ("er2", "ér", "er"),
        ("zuo4", "zuò", "zuo"),
    ]

    def test_tone_mark(self):
        for numbered, marked, _ in self.CASES:
            self.assertEqual(to_tone_mark(numbered), marked, numbered)

    def test_plain(self):
        for numbered, _, plain in self.CASES:
            self.assertEqual(to_plain(numbered), plain, numbered)


class RuleTableTest(unittest.TestCase):
    """字表全部读音与 data/rule_table.tsv 逐项对拍。"""

    @classmethod
    def setUpClass(cls):
        with open(RULE_TABLE_PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        cls.header = lines[0].split("\t")
        cls.rows = [line.split("\t") for line in lines[1:] if line.strip()]

    def test_header(self):
        self.assertEqual(self.header,
                         ["char", "pinyin_numbered", "pinyin_tone", "pinyin_plain"])

    def test_every_row_matches_code(self):
        for char, numbered, tone, plain in self.rows:
            self.assertIn(char, CHAR_TABLE, "规则表含字表外字符: %s" % char)
            self.assertIn(numbered, CHAR_TABLE[char],
                          "规则表读音不在字表中: %s %s" % (char, numbered))
            self.assertEqual(to_tone_mark(numbered), tone,
                             "带声调对拍失败: %s %s" % (char, numbered))
            self.assertEqual(to_plain(numbered), plain,
                             "不带声调对拍失败: %s %s" % (char, numbered))

    def test_rule_table_covers_char_table(self):
        covered = {(row[0], row[1]) for row in self.rows}
        expected = {(ch, r) for ch, readings in CHAR_TABLE.items() for r in readings}
        self.assertEqual(covered, expected,
                         "规则表与字表不一致: 缺少 %s，多余 %s"
                         % (sorted(expected - covered)[:5], sorted(covered - expected)[:5]))


class SingleCharModeTest(unittest.TestCase):
    """单字模式：每字取频率最高的默认读音。"""

    def test_default_reading(self):
        self.assertEqual(pinyin("长", mode="char"), ["cháng"])
        self.assertEqual(pinyin("好", mode="char"), ["hǎo"])
        self.assertEqual(pinyin("的", mode="char"), ["de"])

    def test_plain_style(self):
        self.assertEqual(pinyin("长好", mode="char", style="plain"),
                         ["chang", "hao"])

    def test_unknown_hanzi_passthrough(self):
        self.assertEqual(pinyin("龘", mode="char"), ["龘"])


class PhrasePriorityTest(unittest.TestCase):
    """词组优先：词表读音覆盖单字默认读音。"""

    def test_phrase_overrides_char_default(self):
        # “重”单字默认 zhòng，词组“重庆”中读 chóng
        self.assertEqual(pinyin("重庆", mode="phrase"), ["chóng", "qìng"])
        self.assertEqual(pinyin("重庆", mode="char"), ["zhòng", "qìng"])
        # “长”单字默认 cháng，词组“长大”中读 zhǎng
        self.assertEqual(pinyin("长大", mode="phrase"), ["zhǎng", "dà"])
        self.assertEqual(pinyin("长大", mode="char"), ["cháng", "dà"])

    def test_compound_sentence(self):
        self.assertEqual(
            pinyin("重庆银行行长", mode="phrase"),
            ["chóng", "qìng", "yín", "háng", "háng", "zhǎng"],
        )
        self.assertEqual(
            pinyin("重庆银行行长", mode="char"),
            ["zhòng", "qìng", "yín", "xíng", "xíng", "cháng"],
        )

    def test_plain_style_phrase(self):
        self.assertEqual(pinyin("音乐", style="plain"), ["yin", "yue"])
        self.assertEqual(pinyin("快乐", style="plain"), ["kuai", "le"])

    def test_neutral_tone(self):
        self.assertEqual(pinyin("慢慢地"), ["màn", "màn", "de"])
        self.assertEqual(pinyin("目的"), ["mù", "dì"])

    def test_longest_match(self):
        # “丢三落四”(4字) 优先于任何较短组合
        tokens = segment("丢三落四")
        self.assertEqual(tokens[0][0], "phrase")
        self.assertEqual(tokens[0][1], "丢三落四")
        self.assertEqual(pinyin("丢三落四"), ["diū", "sān", "là", "sì"])


class HeteronymTest(unittest.TestCase):
    """多音字候选：返回完整列表并给出优先级依据。"""

    def test_candidates_ordered_by_frequency(self):
        result = candidates("长")
        self.assertEqual([c["pinyin"] for c in result], ["cháng", "zhǎng"])
        self.assertEqual([c["rank"] for c in result], [1, 2])
        for item in result:
            self.assertTrue(item["basis"], "候选必须带排序依据")

    def test_candidates_have_examples_or_basis(self):
        for ch in ("重", "行", "乐", "和", "差", "着"):
            result = candidates(ch)
            self.assertGreaterEqual(len(result), 2)
            for item in result:
                self.assertTrue(item["basis"])

    def test_candidates_plain_style(self):
        result = candidates("乐", style="plain")
        self.assertEqual([c["pinyin"] for c in result], ["le", "yue"])

    def test_heteronym_output_lists_all(self):
        out = pinyin("长沙", mode="char", heteronym=True)
        self.assertEqual(out[0], ["cháng", "zhǎng"])

    def test_heteronym_context_reading_first(self):
        # 词组模式中，上下文选定的读音排在候选第 1 位
        out = pinyin("长大", heteronym=True)
        self.assertEqual(out[0][0], "zhǎng")
        self.assertEqual(set(out[0]), {"zhǎng", "cháng"})

    def test_single_reading_char_candidates(self):
        result = candidates("庆")
        self.assertEqual(len(result), 1)
        self.assertIn("单音字", result[0]["basis"])

    def test_unknown_char_no_candidates(self):
        self.assertEqual(candidates("龘"), [])


class NonHanziTest(unittest.TestCase):
    """非汉字与全角符号：原样保留。"""

    def test_latin_and_digits(self):
        self.assertEqual(pinyin("abc123"), ["abc123"])

    def test_fullwidth_punctuation(self):
        self.assertEqual(pinyin("，。！？；：（）「」"), ["，。！？；：（）「」"])

    def test_mixed_text(self):
        self.assertEqual(
            pinyin("快乐，world！2026"),
            ["kuài", "lè", "，world！2026"],
        )

    def test_fullwidth_mixed_with_hanzi(self):
        self.assertEqual(
            pinyin("音乐：yīn yuè", mode="phrase"),
            ["yīn", "yuè", "：yīn yuè"],
        )

    def test_empty_string(self):
        self.assertEqual(pinyin(""), [])

    def test_unknown_hanzi_passthrough(self):
        self.assertEqual(pinyin("A龘B"), ["A", "龘", "B"])


class DataConsistencyTest(unittest.TestCase):
    """数据自洽：词表与字表互相印证。"""

    def test_phrase_readings_align_with_chars(self):
        for word, readings in PHRASE_DICT.items():
            self.assertEqual(len(word), len(readings), word)

    def test_phrase_chars_in_char_table(self):
        for word in PHRASE_DICT:
            for ch in word:
                self.assertIn(ch, CHAR_TABLE, "%s 中的 %s 不在字表" % (word, ch))

    def test_phrase_reading_is_valid_reading_of_char(self):
        for word, readings in PHRASE_DICT.items():
            for ch, reading in zip(word, readings):
                self.assertIn(reading, CHAR_TABLE[ch],
                              "%s 中 %s 的读音 %s 不在字表" % (word, ch, reading))

    def test_every_polyphonic_reading_has_phrase_example(self):
        """多音字的每个读音都至少有一个词表示例（保证词表覆盖度）。"""
        from pinyin_lib.core import _phrase_examples
        uncovered = []
        for ch, readings in CHAR_TABLE.items():
            if len(readings) < 2:
                continue
            for reading in readings:
                if not _phrase_examples(ch, reading):
                    uncovered.append((ch, reading))
        self.assertEqual(uncovered, [])


if __name__ == "__main__":
    unittest.main()
