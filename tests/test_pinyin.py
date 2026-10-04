# -*- coding: utf-8 -*-
"""自测：规则表逐项对拍（带调/不带调）+ 边界用例。

运行：python3 -m unittest discover -s tests -v   （在仓库根目录）
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pinyinlib import (CHAR_TABLE, PHRASE_TABLE, candidates, convert,
                       convert_str, explain, heteronym, normalize_fullwidth,
                       strip_tone, to_tone_marks)

RULE_TABLE = os.path.join(os.path.dirname(__file__), 'rule_table.tsv')


def load_rule_table():
    rows = []
    with open(RULE_TABLE, encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            assert len(parts) == 3, 'rule_table.tsv 第 %d 行格式错误' % lineno
            rows.append((parts[0], parts[1], parts[2]))
    return rows


class TestRuleTable(unittest.TestCase):
    """规则表逐项对拍：带声调与不带声调两种输出都必须一致。"""

    def test_marks(self):
        for text, marks, _ in load_rule_table():
            with self.subTest(text=text):
                self.assertEqual(convert_str(text, style='marks'), marks)

    def test_plain(self):
        for text, _, plain in load_rule_table():
            with self.subTest(text=text):
                self.assertEqual(convert_str(text, style='plain'), plain)

    def test_marks_plain_consistent(self):
        """带调结果去掉声调后必须等于不带调结果。"""
        for text, _, _ in load_rule_table():
            with self.subTest(text=text):
                marks = convert(text, style='marks')
                plain = convert(text, style='plain')
                nums = convert(text, style='numbers')
                for m, p, n in zip(marks, plain, nums):
                    if re.fullmatch(r'[a-zü]+[1-5]', n):  # 汉字拼音
                        self.assertEqual(strip_tone(n), p)
                        self.assertEqual(to_tone_marks(n), m)
                    else:  # 非汉字原样块（可能含数字，如 123）
                        self.assertEqual(m, p)
                        self.assertEqual(m, n)


class TestToneMarks(unittest.TestCase):
    """标调规则：a/e 优先，ou 标 o，iu/ui 标后者，轻声不标。"""

    def test_mark_placement(self):
        cases = {
            'hang2': 'háng', 'zhong1': 'zhōng', 'xue2': 'xué',
            'qiu2': 'qiú', 'gui1': 'guī', 'shuo1': 'shuō',
            'lü4': 'lǜ', 'nü3': 'nǚ', 'liu2': 'liú',
            'dui4': 'duì', 'kuai4': 'kuài', 'jiong1': 'jiōng',
        }
        for numbered, expected in cases.items():
            with self.subTest(numbered=numbered):
                self.assertEqual(to_tone_marks(numbered), expected)

    def test_neutral_tone(self):
        self.assertEqual(to_tone_marks('de5'), 'de')
        self.assertEqual(to_tone_marks('zi5'), 'zi')

    def test_strip(self):
        self.assertEqual(strip_tone('lü4'), 'lü')
        self.assertEqual(strip_tone('de5'), 'de')


class TestHeteronym(unittest.TestCase):
    """多音字：候选列表、优先级依据、词组上下文收敛。"""

    def test_candidates_ordered_with_basis(self):
        cands = candidates('行')
        self.assertGreaterEqual(len(cands), 2)
        self.assertEqual([c['pinyin'] for c in cands], ['xíng', 'háng'])
        self.assertEqual([c['rank'] for c in cands], [1, 2])
        for c in cands:
            self.assertIn('频率', c['basis'])
        self.assertIn('银行', cands[1]['examples'])

    def test_candidates_styles(self):
        self.assertEqual(candidates('乐', style='plain')[0]['pinyin'], 'le')
        self.assertEqual(candidates('乐', style='numbers')[1]['pinyin'], 'yue4')

    def test_unknown_char(self):
        self.assertEqual(candidates('龘'), [])

    def test_heteronym_single_char(self):
        self.assertEqual(heteronym('重'), [['zhòng', 'chóng']])

    def test_heteronym_phrase_narrows(self):
        # 词组命中后候选收敛为词表定音
        self.assertEqual(heteronym('银行'), [['yín'], ['háng']])
        self.assertEqual(heteronym('行走'), [['xíng'], ['zǒu']])


class TestModes(unittest.TestCase):
    """单字/词组两种模式及词组优先级的影响。"""

    def test_phrase_beats_char_default(self):
        # 单字模式取默认音 xíng；词组模式命中「银行」得 háng
        self.assertEqual(convert_str('银行', mode='char'), 'yín xíng')
        self.assertEqual(convert_str('银行', mode='phrase'), 'yín háng')

    def test_longest_match(self):
        # 「数一数」整体命中，而非「数一」+「数」
        self.assertEqual(convert_str('数一数'), 'shǔ yī shǔ')

    def test_explain_reasons(self):
        rows = explain('银行')
        self.assertEqual(rows[0]['reason'], '命中词组「银行」规则')
        rows = explain('我')
        self.assertIn('单字默认音', rows[0]['reason'])


class TestEdgeCases(unittest.TestCase):
    """边界：空串、非汉字、全角符号、未收录字。"""

    def test_empty(self):
        self.assertEqual(convert(''), [])
        self.assertEqual(convert_str(''), '')

    def test_pure_ascii(self):
        self.assertEqual(convert('Hello 123'), ['Hello 123'])

    def test_fullwidth_passthrough(self):
        # 全角符号默认原样保留
        self.assertEqual(convert_str('你好，世界！'), 'nǐ hǎo ， shì jiè ！')
        self.assertEqual(convert('，。！？'), ['，。！？'])

    def test_fullwidth_normalize(self):
        self.assertEqual(normalize_fullwidth('ＡＢＣ１２３，'), 'ABC123,')
        self.assertEqual(convert_str('ＡＢＣ中文１２３', normalize=True),
                         'ABC zhōng wén 123')

    def test_unlisted_hanzi_passthrough(self):
        # 词表未收录的汉字不猜读音，原样保留
        self.assertEqual(convert_str('我龘你'), 'wǒ 龘 nǐ')

    def test_mixed_tokens(self):
        self.assertEqual(convert('ABC中文123'),
                         ['ABC', 'zhōng', 'wén', '123'])

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            convert('我', style='bad')
        with self.assertRaises(ValueError):
            convert('我', mode='bad')


class TestDataIntegrity(unittest.TestCase):
    """数据表自洽：词组每字都在单字表内，且词组读音属于该字候选。"""

    def test_phrase_chars_covered(self):
        for phrase, readings in PHRASE_TABLE.items():
            self.assertEqual(len(phrase), len(readings), phrase)
            for ch, reading in zip(phrase, readings):
                with self.subTest(phrase=phrase, char=ch):
                    self.assertIn(ch, CHAR_TABLE)
                    # 轻声(5)是词层面的弱读，允许是候选本调的轻声形式
                    if reading.endswith('5'):
                        self.assertIn(strip_tone(reading),
                                      {strip_tone(r) for r in CHAR_TABLE[ch]})
                    else:
                        self.assertIn(reading, CHAR_TABLE[ch])

    def test_no_duplicate_readings(self):
        for ch, readings in CHAR_TABLE.items():
            self.assertEqual(len(readings), len(set(readings)), ch)


if __name__ == '__main__':
    unittest.main()
