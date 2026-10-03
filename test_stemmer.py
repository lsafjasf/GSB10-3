"""Self-tests: family assertions, edge cases, priority, updatability, metrics."""

import unittest

from evaluate import evaluate
from stemmer import (
    AGGRESSIVE_EXTRA_RULES,
    DEFAULT_RULES,
    LemmaEngine,
    Rule,
)
from word_families import FAMILIES


class FamilyAssertions(unittest.TestCase):
    """Every surface form of a family must land on the family key."""

    def setUp(self):
        self.engine = LemmaEngine()

    def test_all_gold_families(self):
        failures = []
        for expected, forms in FAMILIES.items():
            for form in forms:
                key = self.engine.lemmatize(form)
                if key != expected:
                    failures.append(f"{form!r} -> {key!r}, expected {expected!r}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_family_clusters_collapse_to_single_key(self):
        # representative hand-checked families
        for expected, forms in {
            "go": ["go", "goes", "going", "went", "gone"],
            "be": ["be", "am", "is", "are", "was", "were", "been", "being"],
            "run": ["run", "runs", "running", "ran"],
            "make": ["make", "makes", "making", "made"],
            "child": ["child", "children", "child's", "children's"],
            "mouse": ["mouse", "mice"],
            "study": ["study", "studies", "studied", "studying"],
            "stop": ["stop", "stops", "stopped", "stopping"],
        }.items():
            keys = {self.engine.lemmatize(w) for w in forms}
            self.assertEqual(keys, {expected}, forms)


class RuleInflections(unittest.TestCase):
    def setUp(self):
        self.lemma = LemmaEngine().lemmatize

    def test_plain_suffixes(self):
        cases = {
            "cats": "cat", "dogs": "dog",
            "watched": "watch", "walked": "walk",
            "boxes": "box", "classes": "class",
            "watches": "watch", "goes": "go", "does": "do",
        }
        for word, expected in cases.items():
            self.assertEqual(self.lemma(word), expected, word)

    def test_y_and_e_alternations(self):
        for word, expected in {
            "flies": "fly", "studies": "study", "carries": "carry",
            "studied": "study", "cried": "cry",
            "making": "make", "baking": "bake", "hoped": "hope",
            "dying": "die", "lying": "lie",
        }.items():
            self.assertEqual(self.lemma(word), expected, word)

    def test_doubled_consonants(self):
        for word, expected in {
            "running": "run", "stopping": "stop", "swimming": "swim",
            "sitting": "sit", "beginning": "begin",
        }.items():
            self.assertEqual(self.lemma(word), expected, word)

    def test_no_false_doubling(self):
        # undoubling must only fire for consonants that actually double
        for word, expected in {"falling": "fall", "kissing": "kiss",
                               "walking": "walk", "playing": "play"}.items():
            self.assertEqual(self.lemma(word), expected, word)
        # short stems and s/us/is endings must stay untouched
        for word in {"sing", "king", "ring", "thing",
                     "class", "this", "bus", "gas"}:
            self.assertEqual(self.lemma(word), word, word)

    def test_capitalized_surface_forms(self):
        self.assertEqual(self.lemma("Cats"), "cat")
        self.assertEqual(self.lemma("Went"), "go")
        self.assertEqual(self.lemma("Children"), "child")


class Irregulars(unittest.TestCase):
    def setUp(self):
        self.lemma = LemmaEngine().lemmatize

    def test_irregular_examples(self):
        for word, expected in {
            "went": "go", "mice": "mouse", "geese": "goose",
            "children": "child", "men": "man", "better": "good",
            "was": "be", "were": "be", "took": "take", "ate": "eat",
            "people": "person",
        }.items():
            self.assertEqual(self.lemma(word), expected, word)


class Acronyms(unittest.TestCase):
    def setUp(self):
        self.lemma = LemmaEngine().lemmatize

    def test_uppercase_preserved(self):
        for word in ["USA", "NASA", "JSON", "HTTP", "FAQ", "API", "URL"]:
            self.assertEqual(self.lemma(word), word, word)

    def test_uppercase_plural(self):
        for word, expected in {
            "APIs": "API", "URLs": "URL", "FAQs": "FAQ", "IDs": "ID",
        }.items():
            self.assertEqual(self.lemma(word), expected, word)


class NonEnglish(unittest.TestCase):
    def setUp(self):
        self.lemma = LemmaEngine().lemmatize

    def test_non_ascii_passthrough(self):
        for word in ["café", "naïve", "über", "München", "你好", "東京"]:
            self.assertEqual(self.lemma(word), word, word)

    def test_ascii_vs_accented_stay_distinct(self):
        # accented forms must NOT be merged with their stripped spellings
        self.assertNotEqual(self.lemma("café"), self.lemma("cafe"))
        self.assertNotEqual(self.lemma("naïve"), self.lemma("naive"))

    def test_non_alpha_passthrough(self):
        for word in ["co-operate", "don't", "123", "", "hello世界"]:
            self.assertEqual(self.lemma(word), word, word)

    def test_possessive(self):
        self.assertEqual(self.lemma("dog's"), "dog")
        self.assertEqual(self.lemma("children's"), "child")


class PriorityAndUpdatability(unittest.TestCase):
    def test_exception_beats_rule(self):
        lemma = LemmaEngine().lemmatize
        # "cookies" matches ies->y (would give "cooky") but the exception
        # table wins and restores "cookie"
        self.assertEqual(lemma("cookies"), "cookie")
        self.assertEqual(lemma("went"), "go")

    def test_add_irregular(self):
        engine = LemmaEngine()
        engine.add_irregular("cacti", "cactus")
        self.assertEqual(engine.lemmatize("cacti"), "cactus")

    def test_add_and_remove_rule(self):
        engine = LemmaEngine()
        self.assertEqual(engine.lemmatize("formulae"), "formulae")
        engine.add_rule(Rule("ae", "a", min_stem=3, name="ae->a"))
        self.assertEqual(engine.lemmatize("formulae"), "formula")
        self.assertTrue(engine.remove_rule("ae->a"))
        self.assertEqual(engine.lemmatize("formulae"), "formulae")

    def test_rule_order_is_priority(self):
        # inserting a catch-all at priority 0 shadows everything below it
        engine = LemmaEngine()
        engine.add_rule(Rule("s", "ZZZ", min_stem=1), index=0)
        self.assertEqual(engine.lemmatize("cats"), "catZZZ")

    def test_custom_tables_via_constructor(self):
        engine = LemmaEngine(rules=[Rule("x", "", min_stem=1, name="x->")],
                             irregulars={"foo": "bar"})
        self.assertEqual(engine.lemmatize("foo"), "bar")
        self.assertEqual(engine.lemmatize("abcx"), "abc")


class MergeMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default = evaluate(LemmaEngine(), FAMILIES)
        aggressive = LemmaEngine(rules=DEFAULT_RULES + AGGRESSIVE_EXTRA_RULES)
        cls.aggressive = evaluate(aggressive, FAMILIES)

    def test_conservative_rules_have_zero_over_merging(self):
        self.assertEqual(self.default["incorrect"], 0)
        self.assertEqual(self.default["missed"], 0)
        self.assertEqual(self.default["correct"],
                         self.default["positive_pairs"])

    def test_over_merging_is_measurable(self):
        self.assertGreater(self.aggressive["incorrect"], 0)
        self.assertGreater(self.aggressive["incorrect"],
                           self.default["incorrect"])
        # the aggressive rules demonstrably collapse classic traps
        merged = {tuple(sorted(p)) for p in self.aggressive["incorrect_pairs"]}
        self.assertIn(("universe", "university"), merged)
        self.assertIn(("general", "generous"), merged)
        self.assertIn(("person", "personal"), merged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
