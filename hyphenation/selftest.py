"""Self-tests for the multilingual hyphenation library.

Run from the repository root:

    python3 -m hyphenation.selftest
"""
from __future__ import annotations

import re
import unittest

try:
    from .core import Hyphenator, insert_hyphens, remove_hyphens, SOFT_HYPHEN
except ImportError:  # allow running as a plain script
    from core import Hyphenator, insert_hyphens, remove_hyphens, SOFT_HYPHEN

LANGUAGES = ["en", "de", "zh", "ja", "ko"]

# Extra corpus (beyond the rule-table cases) used by the constraint and
# round-trip sweeps. Includes monosyllables, very long compounds,
# hyphenated words and non-Latin text.
EXTRA_CORPUS = {
    "en": [
        "cat", "dog", "strength", "rhythm", "I", "a",
        "hyphenation", "internationalization", "mother-in-law",
        "well-known", "state-of-the-art",
    ],
    "de": [
        "Dampf", "Haus", "Mutter", "Krankenhaus",
        "Donaudampfschifffahrtsgesellschaftskapitän",
        "Donau-Dampf", "Berlin-Brandenburg",
    ],
    "zh": ["我喜欢自然语言处理。", "《红楼梦》很有趣。", "你好，世界！"],
    "ja": ["私は日本語を勉強しています。", "きょうはいい天気ですね", "「こんにちは」"],
    "ko": ["한국어는세계에서사용됩니다。", "안녕하세요"],
}


def case_text(case: dict) -> str:
    return case.get("word", case.get("text", ""))


def explicit_hyphen_breaks(text: str) -> set[int]:
    """Break positions created by hard hyphens already in the text."""
    return {m.end() for m in re.finditer("-", text) if 0 < m.end() < len(text)}


class RuleCaseTest(unittest.TestCase):
    """Every case shipped in the external rule tables must pass."""

    def test_rule_cases(self):
        failures = []
        for lang in LANGUAGES:
            hyph = Hyphenator(lang)
            for case in hyph.rules.cases:
                text = case_text(case)
                got = hyph.break_points(text)
                if got != case["breaks"]:
                    failures.append(
                        f"{lang}: {text!r} -> got {got}, want {case['breaks']}"
                    )
        self.assertEqual(failures, [])


class MinLengthConstraintTest(unittest.TestCase):
    """No break point may violate the min prefix/suffix constraint."""

    def test_zero_violations(self):
        violations = []
        for lang in LANGUAGES:
            hyph = Hyphenator(lang)
            texts = [case_text(c) for c in hyph.rules.cases] + EXTRA_CORPUS[lang]
            for text in texts:
                explicit = explicit_hyphen_breaks(text)
                for pos in hyph.break_points(text):
                    if pos in explicit:
                        continue  # hard hyphens are exempt by design
                    left, right = pos, len(text) - pos
                    if left < hyph.rules.min_left or right < hyph.rules.min_right:
                        violations.append(
                            f"{lang}: {text!r} break at {pos} "
                            f"(left={left}, right={right}, "
                            f"min={hyph.rules.min_left}/{hyph.rules.min_right})"
                        )
        self.assertEqual(violations, [], msg=f"{len(violations)} violations")


class RoundTripTest(unittest.TestCase):
    """Inserting soft hyphens and removing them restores the original."""

    def test_round_trip(self):
        mismatches = []
        for lang in LANGUAGES:
            hyph = Hyphenator(lang)
            texts = [case_text(c) for c in hyph.rules.cases] + EXTRA_CORPUS[lang]
            for text in texts:
                hyphenated = hyph.hyphenate(text)
                restored = remove_hyphens(hyphenated)
                if restored != text:
                    mismatches.append(f"{lang}: {text!r} -> {restored!r}")
                # Inserted hyphens must appear exactly at break points.
                expected = insert_hyphens(text, hyph.break_points(text))
                if hyphenated != expected:
                    mismatches.append(f"{lang}: insertion mismatch for {text!r}")
        self.assertEqual(mismatches, [])


class EdgeCaseTest(unittest.TestCase):
    def test_monosyllabic_words_have_no_breaks(self):
        for word in ["cat", "dog", "strength", "a", "I"]:
            self.assertEqual(Hyphenator("en").break_points(word), [])
        for word in ["Dampf", "Haus"]:
            self.assertEqual(Hyphenator("de").break_points(word), [])

    def test_very_long_compound(self):
        word = "Donaudampfschifffahrtsgesellschaftskapitän"
        hyph = Hyphenator("de")
        points = hyph.break_points(word)
        self.assertGreaterEqual(len(points), 8)
        # Compound morpheme boundaries must be among the break points.
        for boundary in (5, 10, 16, 22, 35):  # Donau|dampf|schiff|fahrts|gesellschafts|kapitän
            self.assertIn(boundary, points)
        for pos in points:
            self.assertGreaterEqual(pos, hyph.rules.min_left)
            self.assertGreaterEqual(len(word) - pos, hyph.rules.min_right)

    def test_hyphenated_words(self):
        hyph = Hyphenator("en")
        points = hyph.break_points("mother-in-law")
        self.assertIn(7, points)   # after the first hard hyphen
        self.assertIn(10, points)  # after the second hard hyphen
        self.assertIn(4, points)   # moth-er inside the first component
        # Components too short for the min-length rule get no inner breaks.
        self.assertNotIn(8, points)  # inside "in"

    def test_non_latin_scripts(self):
        zh = Hyphenator("zh").break_points("我喜欢自然语言处理。")
        self.assertNotIn(9, zh)  # never break before "。"
        ja = Hyphenator("ja").break_points("きょうはいい天気ですね")
        self.assertNotIn(1, ja)  # never break before small kana "ょ"
        ko = Hyphenator("ko").break_points("안녕하세요")
        self.assertEqual(ko, [1, 2, 3, 4])

    def test_soft_hyphen_is_the_default_marker(self):
        hyphenated = Hyphenator("en").hyphenate("computer")
        self.assertEqual(hyphenated, "com" + SOFT_HYPHEN + "put" + SOFT_HYPHEN + "er")


if __name__ == "__main__":
    unittest.main(verbosity=2)
