"""Self tests for the multilingual hyphenator (unittest, stdlib only)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hyphenator import CORPUS_PATH, RULES_DIR, SOFT_HYPHEN, Hyphenator
from hyphenator.coverage import build_report

ROOT = Path(__file__).resolve().parent.parent
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
LONG_GERMAN = "Donaudampfschifffahrtsgesellschaftskapitän"


def dash(word, lang):
    return Hyphenator(lang).hyphenate(word, hyphen="-")


class TestEnglish(unittest.TestCase):
    def test_syllable_rules(self):
        self.assertEqual(dash("letter", "en"), "let-ter")      # VCCV
        self.assertEqual(dash("running", "en"), "run-ning")   # VCCV + -ing
        self.assertEqual(dash("melon", "en"), "me-lon")       # VCV
        self.assertEqual(dash("paper", "en"), "pa-per")       # VCV
        self.assertEqual(dash("action", "en"), "ac-tion")     # VCCV + -tion
        self.assertEqual(dash("unhappy", "en"), "un-hap-py")  # prefix + VCCV
        self.assertEqual(dash("preheat", "en"), "pre-heat")   # prefix
        self.assertEqual(dash("quickly", "en"), "quick-ly")   # suffix
        self.assertEqual(dash("darkness", "en"), "dark-ness")
        self.assertEqual(dash("thankful", "en"), "thank-ful")
        self.assertEqual(dash("speechless", "en"), "speech-less")

    def test_exceptions(self):
        self.assertEqual(dash("hyphenation", "en"), "hy-phen-ation")
        self.assertEqual(dash("computer", "en"), "com-pu-ter")
        self.assertEqual(dash("development", "en"), "de-vel-op-ment")

    def test_monosyllables_have_no_break(self):
        for word in ["cat", "dog", "tree", "strength", "thought"]:
            self.assertEqual(Hyphenator("en").breakpoints(word), [], word)


class TestGerman(unittest.TestCase):
    def test_syllable_rules(self):
        self.assertEqual(dash("Vater", "de"), "Va-ter")
        self.assertEqual(dash("Mutter", "de"), "Mut-ter")
        self.assertEqual(dash("kommen", "de"), "kom-men")
        self.assertEqual(dash("Muster", "de"), "Mus-ter")
        self.assertEqual(dash("Tasche", "de"), "Ta-sche")
        self.assertEqual(dash("sprechen", "de"), "spre-chen")
        self.assertEqual(dash("welche", "de"), "wel-che")
        self.assertEqual(dash("Zucker", "de"), "Zuc-ker")  # ck -> k-k

    def test_compounds(self):
        self.assertEqual(dash("Krankenhaus", "de"), "Kran-ken-haus")
        self.assertEqual(dash("Autobahn", "de"), "Au-to-bahn")
        self.assertEqual(dash("Kindergarten", "de"), "Kin-der-gar-ten")

    def test_super_long_compound(self):
        out = dash(LONG_GERMAN, "de")
        # Fugen-s stays with the preceding part (fahrts-, schafts-)
        self.assertEqual(
            out,
            "Do-nau-dampf-schiff-fahrts-ge-sell-schafts-ka-pi-tän",
        )
        self.assertGreaterEqual(len(out.split("-")), 10)


class TestCJK(unittest.TestCase):
    def test_chinese(self):
        self.assertEqual(dash("自然语言处理", "zh"), "自-然-语-言-处-理")
        self.assertEqual(dash("你好，世界！", "zh"), "你-好，-世-界！")

    def test_japanese_kinsoku(self):
        # small kana ょ cannot start a line
        self.assertEqual(dash("きょうと", "ja"), "きょ-う-と")
        self.assertEqual(dash("こんにちは、世界！", "ja"),
                         "こ-ん-に-ち-は、-世-界！")

    def test_korean(self):
        self.assertEqual(dash("안녕하세요", "ko"), "안-녕-하-세-요")


class TestEdgeCases(unittest.TestCase):
    def test_empty_and_single_char(self):
        for lang in ("en", "de", "zh", "ja", "ko"):
            h = Hyphenator(lang)
            self.assertEqual(h.breakpoints(""), [])
            self.assertEqual(h.breakpoints("x"), [])
            self.assertEqual(h.hyphenate(""), "")
            self.assertEqual(Hyphenator.restore(h.hyphenate("x")), "x")

    def test_words_with_hyphens(self):
        h = Hyphenator("en")
        # segments are hyphenated independently, real hyphen is preserved
        self.assertEqual(h.breakpoints("well-known"), [])
        self.assertEqual(
            h.hyphenate("computer-generated", hyphen="-"),
            "com-pu-ter-ge-ne-ra-ted",
        )
        self.assertNotIn(SOFT_HYPHEN, h.hyphenate("well-known"))

    def test_non_latin_mixed_punctuation(self):
        h = Hyphenator("zh")
        breaks = h.breakpoints("你好，世界！")
        self.assertNotIn(2, breaks)   # before ，
        self.assertNotIn(5, breaks)   # before ！

    def test_short_words_constraint_filters_breaks(self):
        # V|CV would suggest a break at index 1, but min prefix/suffix = 2
        self.assertEqual(Hyphenator("en").breakpoints("ivy"), [])
        self.assertEqual(Hyphenator("de").breakpoints("Oma"), [])
        self.assertEqual(Hyphenator("de").breakpoints("Esel"), [])


class TestRoundTrip(unittest.TestCase):
    def test_restore_equals_original_for_entire_corpus(self):
        for lang, words in CORPUS.items():
            h = Hyphenator(lang)
            for word in words:
                marked = h.hyphenate(word)            # soft hyphens
                self.assertEqual(Hyphenator.restore(marked), word, (lang, word))

    def test_roundtrip_with_real_hyphens(self):
        h = Hyphenator("en")
        for word in ["well-known", "computer-generated", "run-ning"]:
            marked = h.hyphenate(word)
            self.assertEqual(Hyphenator.restore(marked), word)
            # real hyphens survive, only soft hyphens are stripped
            self.assertIn("-", Hyphenator.restore(marked))


class TestConstraintsZeroViolations(unittest.TestCase):
    def test_no_min_prefix_suffix_violation_in_corpus(self):
        total = 0
        for lang, words in CORPUS.items():
            h = Hyphenator(lang)
            for word in words:
                violations = h.validate(word)
                self.assertEqual(violations, [], (lang, word, violations))
                total += 1
        self.assertGreater(total, 30, "the constraint assertion ran on real cases")

    def test_violation_detection_does_detect_violations(self):
        # sanity check: a hand-crafted break at index 1 IS a violation for en
        violations = Hyphenator("en").validate("lemon", breaks=[1])
        self.assertTrue(any(v["kind"] == "min_prefix" for v in violations))


class TestRuleCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_report()
        cls.out = ROOT / "coverage_report.json"
        cls.out.write_text(
            json.dumps(cls.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_every_rule_is_covered_by_at_least_one_case(self):
        for lang in CORPUS:
            spec = json.loads(
                (RULES_DIR / (lang + ".json")).read_text(encoding="utf-8")
            )
            counts = self.report["languages"][lang]["rule_coverage"]
            for rule in spec["rules"]:
                self.assertIn(
                    rule["id"], counts,
                    "rule %s/%s never fires" % (lang, rule["id"]),
                )
                self.assertGreaterEqual(counts[rule["id"]], 1)

    def test_exception_and_morpheme_categories_are_counted(self):
        en_counts = self.report["languages"]["en"]["rule_coverage"]
        de_counts = self.report["languages"]["de"]["rule_coverage"]
        self.assertGreaterEqual(en_counts["exceptions"], 8)
        self.assertGreaterEqual(de_counts["de-compound"], 4)

    def test_report_file_written(self):
        self.assertTrue(self.out.exists())


class TestRulesExternalAndUpdatable(unittest.TestCase):
    def test_rules_can_be_overridden_from_outside_the_package(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(RULES_DIR, tmp / "rules")
            spec_path = tmp / "rules" / "en.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            # external update: add a new exception without editing the engine
            spec["exceptions"]["blorp"] = "blor-p"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            default = Hyphenator("en")
            self.assertEqual(default.breakpoints("blorp"), [])
            updated = Hyphenator("en", rules_dir=tmp / "rules")
            self.assertEqual(updated.hyphenate("blorp", hyphen="-"), "blor-p")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
