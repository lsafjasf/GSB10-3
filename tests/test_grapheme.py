#!/usr/bin/env python3
"""Self-tests for grapheme cluster segmentation and cluster-aware editing.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import editor
from grapheme import (boundaries, is_boundary, next_boundary, prev_boundary,
                      segment, segment_reverse)

HERE = os.path.dirname(__file__)


def parse_official_tests(path):
    """Yield (codepoints, break_offsets) from GraphemeBreakTest.txt lines."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            assert tokens[0] == "÷" and tokens[-1] == "÷"
            cps, breaks, pos = [], [0], 0
            for tok in tokens[1:-1]:
                if tok == "÷":
                    breaks.append(pos)
                elif tok == "×":
                    pass
                else:
                    cps.append(int(tok, 16))
                    pos += 1
            breaks.append(pos)
            yield "".join(map(chr, cps)), breaks


class OfficialDataTest(unittest.TestCase):
    """Every case in the Unicode Consortium's GraphemeBreakTest.txt."""

    def test_all_official_cases(self):
        path = os.path.join(HERE, "GraphemeBreakTest.txt")
        cases = list(parse_official_tests(path))
        self.assertGreater(len(cases), 500)
        for text, breaks in cases:
            with self.subTest(text=" ".join(f"{ord(c):04X}" for c in text)):
                self.assertEqual(boundaries(text), breaks)
                self.assertEqual(segment_reverse(text), segment(text))


class SegmentationTest(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(segment("hello"), list("hello"))

    def test_empty(self):
        self.assertEqual(segment(""), [])
        self.assertEqual(segment_reverse(""), [])
        self.assertEqual(boundaries(""), [0])

    def test_combining_marks(self):
        self.assertEqual(segment("é"), ["é"])          # e + U+0301
        self.assertEqual(segment("a̧"), ["a̧"])       # a + U+0301 U+0327
        self.assertEqual(segment("각"), ["각"])     # Hangul L V T
        self.assertEqual(segment("கு"), ["கு"])         # base + spacing mark

    def test_emoji(self):
        self.assertEqual(segment("👍🏽"), ["👍🏽"])          # skin-tone modifier
        self.assertEqual(segment("👨‍👩‍👧‍👦"), ["👨‍👩‍👧‍👦"])  # ZWJ sequence
        self.assertEqual(segment("1️⃣"), ["1️⃣"])            # keycap
        self.assertEqual(segment("✈️"), ["✈️"])            # variation selector
        self.assertEqual(segment("❤︎"), ["❤︎"])

    def test_flags(self):
        self.assertEqual(segment("🇨🇳"), ["🇨🇳"])
        self.assertEqual(segment("🇨🇳🇺🇸"), ["🇨🇳", "🇺🇸"])   # RI pairs
        self.assertEqual(segment("🇦🇧🇨"), ["🇦🇧", "🇨"])     # odd run: pair + one

    def test_bidi_controls_are_isolated(self):
        text = "ab\u202Ecd\u202C"
        self.assertEqual(segment(text), ["a", "b", "\u202E", "c", "d", "\u202C"])

    def test_crlf(self):
        self.assertEqual(segment("\r\n"), ["\r\n"])
        self.assertEqual(segment("\r\n\n"), ["\r\n", "\n"])

    def test_prepend(self):
        self.assertEqual(segment("؀a"), ["؀a"])          # ARABIC NUMBER SIGN

    def test_join_equals_source(self):
        text = "á👨‍👩‍👧‍👦🇨🇳🇺🇸x\u202E✈️"
        self.assertEqual("".join(segment(text)), text)
        self.assertEqual("".join(segment_reverse(text)), text)


class RoundTripTest(unittest.TestCase):
    """Reverse segmentation must equal forward segmentation."""

    POOL = (
        "abcXYZ 09\t"
        "é" "é" "a̧" "क" "कु" "각" "ᄀ" "ᅡ" "ᆨ"
        "👨‍👩‍👧‍👦" "👍🏽" "❤️" "✈️" "1️⃣" "🏳️‍🌈"
        "🇨🇳" "🇺🇸" "🇦" "🇧"
        "\u202E" "\u202C" "\u200D" "\u200C" "\uFE0F" "\u0301" "\u0327"
        "؀" "\r\n" "\r" "\n"
    )

    def test_reverse_matches_forward_random(self):
        rng = random.Random(20261004)
        for _ in range(3000):
            text = "".join(rng.choice(self.POOL) for _ in range(rng.randrange(0, 25)))
            with self.subTest(text=text):
                fwd = segment(text)
                rev = segment_reverse(text)
                self.assertEqual(fwd, rev)
                self.assertEqual("".join(fwd), text)

    def test_boundary_navigation_consistent_random(self):
        rng = random.Random(7)
        for _ in range(2000):
            text = "".join(rng.choice(self.POOL) for _ in range(rng.randrange(0, 20)))
            b = boundaries(text)
            with self.subTest(text=text):
                # walking right from 0 visits every boundary in order
                pos, seen = 0, [0]
                while pos < len(text):
                    pos = next_boundary(text, pos)
                    seen.append(pos)
                self.assertEqual(seen, b)
                # walking left from the end visits them in reverse
                pos, seen = len(text), [len(text)]
                while pos > 0:
                    pos = prev_boundary(text, pos)
                    seen.append(pos)
                self.assertEqual(seen, b[::-1])
                # next/prev are inverse around every boundary
                for bp in b[1:]:
                    self.assertEqual(prev_boundary(text, bp), b[b.index(bp) - 1])
                for bp in b[:-1]:
                    self.assertEqual(next_boundary(text, bp), b[b.index(bp) + 1])


class EditorTest(unittest.TestCase):
    def states(self, text, ops):
        return [state for _, state in editor.run(ops, text)]

    def test_ascii_sequence(self):
        self.assertEqual(
            self.states("hello", [("move_left",), ("move_left",),
                                  ("backspace",), ("insert", "L")]),
            ["hello│", "hell│o", "hel│lo", "he│lo", "heL│lo"])

    def test_combining_cluster_moves_as_one(self):
        self.assertEqual(
            self.states("a̧x", [("move_left",), ("backspace",)]),
            ["a̧x│", "a̧│x", "│x"])

    def test_emoji_clusters_move_as_one(self):
        self.assertEqual(
            self.states("👨‍👩‍👧‍👦🇨🇳", [("move_left",), ("move_right",),
                                       ("backspace",), ("backspace",)]),
            ["👨‍👩‍👧‍👦🇨🇳│", "👨‍👩‍👧‍👦│🇨🇳", "👨‍👩‍👧‍👦🇨🇳│", "👨‍👩‍👧‍👦│", "│"])

    def test_bidi_control_deleted_alone(self):
        # control chars move/delete as singleton clusters; backspace at the
        # boundary before the RLO removes just 'b', not the control.
        self.assertEqual(
            self.states("ab\u202Ecd", [("move_left",), ("move_left",),
                                       ("move_left",), ("backspace",)]),
            ["ab\u202Ecd│", "ab\u202Ec│d", "ab\u202E│cd", "ab│\u202Ecd",
             "a│\u202Ecd"])

    def test_deleting_control_merges_neighbors_safely(self):
        # a <RLO> ZWJ ZWJ k : deleting the RLO lets the two ZWJs attach to
        # 'a'; the cursor snaps to the start of the merged cluster instead
        # of landing inside it.
        ed = editor.Editor("a\u202E\u200d\u200dk")
        ed.move_left(); ed.move_left(); ed.move_left()  # cursor -> 1 (after 'a')
        ed.delete_forward()                              # remove the RLO
        self.assertEqual(ed.text, "a\u200d\u200dk")
        self.assertEqual(ed.cursor, 0)

    def test_cursor_never_splits_cluster_random(self):
        rng = random.Random(99)
        pool = RoundTripTest.POOL
        for _ in range(500):
            text = "".join(rng.choice(pool) for _ in range(rng.randrange(1, 15)))
            ed = editor.Editor(text)
            for _ in range(rng.randrange(1, 30)):
                op = rng.choice(["move_left", "move_right", "backspace",
                                 "delete_forward"])
                getattr(ed, op)()
                with self.subTest(text=text, op=op):
                    self.assertTrue(is_boundary(ed.text, ed.cursor))
                    self.assertTrue(all(is_boundary(ed.text, b)
                                        for b in boundaries(ed.text)))


if __name__ == "__main__":
    unittest.main()
