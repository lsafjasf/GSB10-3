"""边界用例与核心不变量测试（标准库 unittest）。

运行：python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from examples.demo_doc import build_demo_blocks
from heading_splitter import (
    DEMOTE,
    FILL,
    Block,
    dump_source,
    parse_source,
    rebuild_source,
    split_document,
)
from heading_splitter.clues import numbering_clue

BODY = dict(size=12, indent=24, centered=False)
L1 = dict(size=18, indent=0, centered=False)
L2 = dict(size=16, indent=12, centered=False)
L3 = dict(size=14, indent=18, centered=False)


def body(text):
    return Block(text, **BODY)


def heading(text, style):
    return Block(text, **style)


def levels(result, strategy=FILL):
    return [rec.final_level for rec in result.comparisons[strategy] if not rec.synthetic]


def raw_levels(result):
    return [h.raw_level for h in result.headings]


class TestSourceFormat(unittest.TestCase):
    def test_roundtrip_char_identical(self):
        blocks = [
            Block("标题", 18, 0, False),
            Block("浮点字号", 13.5, 24, False),
            Block("未知字段", None, None, None),
            Block("居中", 18, None, True),
        ]
        source = dump_source(blocks)
        self.assertEqual(parse_source(source), blocks)
        self.assertEqual(dump_source(parse_source(source)), source)

    def test_requires_trailing_newline(self):
        with self.assertRaises(ValueError):
            parse_source("标题\t18\t0\tleft")

    def test_reject_tab_in_text(self):
        with self.assertRaises(ValueError):
            dump_source([Block("含\t制表符", 12, 24, False)])

    def test_empty_document(self):
        self.assertEqual(parse_source("\n"), [])
        result = split_document([])
        self.assertEqual(result.headings, [])
        self.assertEqual(rebuild_source(result), dump_source([]))


class TestNumberingClue(unittest.TestCase):
    def test_convention_table(self):
        cases = [
            ("第一章 概述", 1), ("附录A 参考", 1),
            ("1.1 背景", 2), ("1.1.1 术语", 3), ("2.4.1.1 深层", 4),
            ("一、总则", 2), ("（一）细则", 3), ("1. 要点", 3),
            ("（1）子项", 4), ("① 圈注", 5),
        ]
        for text, expected in cases:
            clue, _ = numbering_clue(text)
            self.assertIsNotNone(clue, text)
            self.assertEqual(clue.level, expected, text)

    def test_no_numbering(self):
        clue, decimal = numbering_clue("普通正文")
        self.assertIsNone(clue)
        self.assertFalse(decimal)

    def test_decimal_like_flag(self):
        _, decimal = numbering_clue("1.5 倍速")
        self.assertTrue(decimal)
        _, decimal = numbering_clue("1.10 版本")
        self.assertFalse(decimal)


class TestEdgeCases(unittest.TestCase):
    def test_no_headings(self):
        """无标题：全部正文，区间为空，重建逐字符一致。"""
        blocks = [body("第一段。"), body("第二段。"), body("第三段。")]
        result = split_document(blocks)
        self.assertEqual(result.headings, [])
        self.assertEqual(result.intervals, [])
        self.assertEqual(result.preamble, (0, 3))
        self.assertEqual(rebuild_source(result), dump_source(blocks))
        self.assertEqual(rebuild_source(result, DEMOTE), dump_source(blocks))

    def test_only_level1_headings(self):
        """只有一级标题：全部判为 L1，无跳级。"""
        blocks = [
            heading("第一章 概述", L1), body("正文一。"),
            heading("第二章 安装", L1), body("正文二。"),
            heading("第三章 使用", L1), body("正文三。"),
        ]
        result = split_document(blocks)
        self.assertEqual(raw_levels(result), [1, 1, 1])
        self.assertEqual(levels(result), [1, 1, 1])
        self.assertEqual(levels(result, DEMOTE), [1, 1, 1])
        for rec in result.comparisons[FILL]:
            self.assertNotIn("跳级", rec.action)
        self.assertEqual(rebuild_source(result), dump_source(blocks))

    def test_large_jump_fill(self):
        """大幅跳级（L1 直接到 L4）：补齐策略插入 L2、L3 两个虚拟标题。"""
        blocks = [
            heading("第一章 概述", L1), body("引言。"),
            Block("1.1.1.1 深层细节", **BODY), body("细节正文。"),
        ]
        result = split_document(blocks)
        self.assertEqual(raw_levels(result), [1, 4])
        self.assertEqual(levels(result, FILL), [1, 4])
        synthetic = [r for r in result.comparisons[FILL] if r.synthetic]
        self.assertEqual([r.final_level for r in synthetic], [2, 3])
        # 章节树中虚拟节点真实存在，但不占任何块
        tree = result.trees[FILL]
        chapter = tree.children[0]
        self.assertEqual([c.level for c in chapter.children], [2])
        synth2 = chapter.children[0]
        self.assertTrue(synth2.synthetic)
        self.assertEqual(synth2.children[0].level, 3)
        self.assertTrue(synth2.children[0].synthetic)
        self.assertEqual(rebuild_source(result, FILL), dump_source(blocks))

    def test_large_jump_demote(self):
        """大幅跳级：降级策略压到上一层级+1，且不新增节点。"""
        blocks = [
            heading("第一章 概述", L1), body("引言。"),
            Block("1.1.1.1 深层细节", **BODY), body("细节正文。"),
        ]
        result = split_document(blocks)
        self.assertEqual(levels(result, DEMOTE), [1, 2])
        self.assertFalse(any(r.synthetic for r in result.comparisons[DEMOTE]))
        demote_record = result.comparisons[DEMOTE][-1]
        self.assertIn("降级", demote_record.action)
        self.assertEqual(rebuild_source(result, DEMOTE), dump_source(blocks))

    def test_heading_in_middle_of_body(self):
        """标题出现在正文中间：无编号、仅缩进线索也能识别，且只切走自己的区间。"""
        blocks = [
            body("前置段落一。"), body("前置段落二。"),
            Block("注意事项", size=12, indent=12, centered=False),
            body("后续段落一。"), body("后续段落二。"),
        ]
        result = split_document(blocks)
        self.assertEqual(len(result.headings), 1)
        self.assertEqual(result.headings[0].block_index, 2)
        self.assertEqual(result.headings[0].votes["position"]["level"], 1)
        self.assertEqual(result.preamble, (0, 2))
        self.assertEqual(result.intervals, [(1, 2, 2, 5)])
        self.assertEqual(rebuild_source(result), dump_source(blocks))

    def test_preamble_before_first_heading(self):
        blocks = [body("前言第一段。"), body("前言第二段。"),
                  heading("第一章 概述", L1), body("正文。")]
        result = split_document(blocks)
        self.assertEqual(result.preamble, (0, 2))
        self.assertEqual(rebuild_source(result), dump_source(blocks))


class TestClueConflicts(unittest.TestCase):
    def test_high_numbering_beats_position(self):
        """高置信编号 vs 居中位置：取编号，且给出取舍依据。"""
        blocks = [
            heading("第一章 概述", L1), body("引言。"),
            Block("1.1.1.1 深层细节", size=12, indent=24, centered=True),
            body("细节正文。"),
        ]
        result = split_document(blocks)
        target = result.headings[-1]
        self.assertEqual(target.raw_level, 4)
        self.assertEqual(target.votes["position"]["level"], 1)
        self.assertIn("冲突", target.reason)
        self.assertIn("高置信编号", target.reason)

    def test_strong_size_beats_medium_numbering(self):
        """字号强线索 vs 中置信编号：取字号。"""
        blocks = [
            heading("第一章 概述", L1), body("引言。"),
            Block("（三）补充说明", **L2), body("补充正文。"),
        ]
        result = split_document(blocks)
        target = result.headings[-1]
        self.assertEqual(target.raw_level, 2)
        self.assertEqual(target.votes["numbering"]["level"], 3)
        self.assertIn("冲突", target.reason)

    def test_medium_numbering_beats_weak_size(self):
        """中置信编号 vs 字号弱线索（差 < 2pt）：取编号。"""
        blocks = [
            body("引言。"),
            Block("1.1 背景", size=13, indent=24, centered=False), body("正文。"),
        ]
        result = split_document(blocks)
        target = result.headings[-1]
        self.assertEqual(target.raw_level, 2)
        self.assertEqual(target.votes["size"]["level"], 1)
        self.assertEqual(target.votes["size"]["weight"], 1.0)
        self.assertIn("冲突", target.reason)

    def test_decimal_noise_suppressed(self):
        """疑似小数且无布局佐证：抑制为正文并记录原因。"""
        blocks = [heading("第一章 概述", L1), body("引言。"),
                  body("1.5 倍速播放说明"), body("本文结束。")]
        result = split_document(blocks)
        self.assertEqual(len(result.headings), 1)
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(result.suppressed[0]["block_index"], 2)
        self.assertEqual(rebuild_source(result), dump_source(blocks))

    def test_decimal_like_with_layout_support_kept(self):
        """疑似小数但有字号佐证：保留为标题。"""
        blocks = [heading("第一章 概述", L1), body("引言。"),
                  Block("2.1 环境要求", **L2), body("正文。")]
        result = split_document(blocks)
        self.assertEqual(raw_levels(result), [1, 2])
        self.assertEqual(result.suppressed, [])

    def test_trailing_punctuation_not_heading(self):
        """以句读结尾的长句不判为标题。"""
        blocks = [heading("第一章 概述", L1),
                  body("1.1 背景介绍如下。"), body("正文。")]
        result = split_document(blocks)
        self.assertEqual(len(result.headings), 1)


class TestDemoDocument(unittest.TestCase):
    def setUp(self):
        self.blocks = build_demo_blocks()
        self.source = dump_source(self.blocks)
        self.result = split_document(parse_source(self.source))

    def test_rebuild_char_identical_both_strategies(self):
        """核心断言：切分结果可逐字符重建回原文。"""
        self.assertEqual(rebuild_source(self.result, FILL), self.source)
        self.assertEqual(rebuild_source(self.result, DEMOTE), self.source)

    def test_intervals_partition(self):
        """前置区间 + 章节区间恰好不重不漏覆盖全部块。"""
        covered = set(range(*self.result.preamble))
        for _level, _idx, lo, hi in self.result.intervals:
            chunk = set(range(lo, hi))
            self.assertFalse(chunk & covered)
            covered |= chunk
        self.assertEqual(covered, set(range(len(self.blocks))))

    def test_expected_levels(self):
        self.assertEqual(
            raw_levels(self.result),
            [1, 1, 2, 3, 3, 2, 1, 2, 3, 3, 2, 3, 3, 2, 2, 2, 4,
             1, 2, 3, 3, 2, 2, 1, 1, 2, 2],
        )

    def test_jump_detected_and_repaired(self):
        fill = self.result.comparisons[FILL]
        jumps = [r for r in fill if "跳级" in r.action and not r.synthetic]
        self.assertEqual(len(jumps), 1)
        self.assertEqual((jumps[0].raw_level, jumps[0].final_level), (4, 4))
        synthetic = [r for r in fill if r.synthetic]
        self.assertEqual([r.final_level for r in synthetic], [3])
        demote = self.result.comparisons[DEMOTE]
        demoted = [r for r in demote if "降级" in r.action]
        self.assertEqual(len(demoted), 1)
        self.assertEqual(demoted[0].final_level, 3)

    def test_conflicts_recorded(self):
        by_text = {h.text: h for h in self.result.headings}
        self.assertIn("冲突", by_text["2.1 环境要求"].reason)      # 编号 vs 居中
        self.assertIn("冲突", by_text["2.4.1.1 深层流程"].reason)  # 高置信编号 vs 位置
        self.assertIn("冲突", by_text["（三）补充说明"].reason)     # 字号强 vs 编号中
        self.assertNotIn("冲突", by_text["第一章 概述"].reason)

    def test_suppressed_decimal(self):
        self.assertEqual(len(self.result.suppressed), 1)
        self.assertEqual(self.result.suppressed[0]["text"], "1.5 倍速播放说明")


if __name__ == "__main__":
    unittest.main()
