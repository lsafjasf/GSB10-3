"""Lemmatizer / stemmer library (Python 3 standard library only).

A *family key* is the string every inflected form of a word maps to, so
search can match different surface forms of the same lexeme.

Merging priority (highest first)
---------------------------------
1. Possessive "'s" stripping (preprocessing): "child's" -> "child".
2. Acronym handling: all-caps tokens are kept as-is ("USA" -> "USA");
   an all-caps stem followed by a lowercase "s" loses only the "s"
   ("APIs" -> "API").
3. Tokens containing non-ASCII or non-alphabetic characters are returned
   unchanged ("café", "你好", "co-operate") -- never crash, never merge.
4. Irregular / exception table: an exact (lower-cased) match beats every
   rule ("went" -> "go", "cookies" -> "cookie" even though the "ies->y"
   rule would produce "cooky").
5. Ordered rule table: the first matching rule wins, so more specific
   (longer-suffix) rules must be listed before general ones
   ("sses->ss" before "s->").
6. Fallback: the lower-cased token itself.

Both tables are updatable at runtime via ``add_irregular`` / ``add_rule`` /
``remove_rule``, or replaceable through the constructor.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

VOWELS = set("aeiou")

# Consonants that commonly double before -ing/-ed (run->running, stop->stopped).
# Notably excludes l/s/f/z so "falling" -> "fall" and "kissing" -> "kiss".
_DOUBLING_CONSONANTS = set("bdgmnprt")


def _is_consonant(ch: str) -> bool:
    return ch.isalpha() and ch not in VOWELS


def _silent_e_stem(stem: str) -> bool:
    """Stem looks like CVC where a silent 'e' was dropped before -ing/-ed.

    making -> mak  (restore e -> make)   hopping -> hopp (excluded: doubled)
    walking -> walk (excluded: no single vowel before final consonant)
    """
    return (
        len(stem) >= 3
        and _is_consonant(stem[-1])
        and stem[-1] not in "wxy"
        and stem[-1] != stem[-2]
        and stem[-2] in VOWELS
        and _is_consonant(stem[-3])
    )


def _es_plural_stem(stem: str) -> bool:
    """Nouns that take -es rather than -s: boxes, watches, dishes, goes."""
    return stem.endswith(("x", "z", "o", "ch", "sh"))


def _s_plural_stem(stem: str) -> bool:
    # Protect "this", "bus", "class" from losing their final s.
    return not stem.endswith(("s", "u", "i"))


@dataclass
class Rule:
    """One suffix-rewrite rule.

    suffix:      suffix to strip from the lower-cased token.
    replacement: string appended to the stem after stripping.
    min_stem:    minimum stem length required for the rule to fire.
    condition:   optional predicate on the stem; rule fires only if True.
    undouble:    if True, drop a trailing doubled consonant produced by
                 stripping (running -> runn -> run).
    name:        human-readable identifier, used by ``remove_rule``.
    """

    suffix: str
    replacement: str = ""
    min_stem: int = 2
    condition: Optional[Callable[[str], bool]] = None
    undouble: bool = False
    name: str = ""

    def apply(self, word: str) -> Optional[str]:
        if not word.endswith(self.suffix):
            return None
        stem = word[: len(word) - len(self.suffix)]
        if len(stem) < self.min_stem:
            return None
        if self.condition is not None and not self.condition(stem):
            return None
        result = stem + self.replacement
        if (
            self.undouble
            and len(result) >= 2
            and result[-1] == result[-2]
            and result[-1] in _DOUBLING_CONSONANTS
        ):
            result = result[:-1]
        return result


# Ordered: the first matching rule wins. Longer/more specific suffixes first.
DEFAULT_RULES: List[Rule] = [
    # dying -> die, lying -> lie; stem must be a single letter so
    # flying/buying/playing fall through to the plain -ing rules
    Rule("ying", "ie", min_stem=1, condition=lambda s: len(s) == 1, name="ying->ie"),
    Rule("ied", "y", min_stem=2, name="ied->y"),                        # studied -> study
    Rule("ies", "y", min_stem=2, name="ies->y"),                        # flies -> fly
    Rule("sses", "ss", min_stem=1, name="sses->ss"),                    # classes -> class
    Rule("ing", "e", min_stem=3, condition=_silent_e_stem, name="ing->e"),  # making -> make
    Rule("ing", "", min_stem=3, undouble=True, name="ing->"),           # running -> run
    Rule("ed", "e", min_stem=3, condition=_silent_e_stem, name="ed->e"),    # baked -> bake
    Rule("ed", "", min_stem=3, undouble=True, name="ed->"),             # stopped -> stop
    Rule("es", "", min_stem=2, condition=_es_plural_stem, name="es->"),     # boxes -> box
    Rule("s", "", min_stem=3, condition=_s_plural_stem, name="s->"),        # cats -> cat
]

# Deliberately over-aggressive derivational rules, used only to demonstrate
# that over-merging is measurable (see evaluate.py). Not part of the default.
AGGRESSIVE_EXTRA_RULES: List[Rule] = [
    Rule("ity", "", min_stem=3, name="ity->"),    # university -> univers
    Rule("ness", "", min_stem=3, name="ness->"),  # happiness -> happy
    Rule("ment", "", min_stem=3, name="ment->"),
    Rule("ous", "", min_stem=3, name="ous->"),    # generous -> gener
    Rule("al", "", min_stem=3, name="al->"),      # personal -> person
    Rule("e", "", min_stem=3, name="e->"),        # universe -> univers
]

DEFAULT_IRREGULARS: Dict[str, str] = {
    # be / go / do / have
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be",
    "been": "be", "being": "be",
    "went": "go", "gone": "go", "going": "go",
    "doing": "do", "done": "do", "did": "do",
    "has": "have", "had": "have",
    # strong verbs
    "made": "make", "took": "take", "ran": "run", "swam": "swim",
    "swum": "swim", "wrote": "write", "written": "write", "ate": "eat",
    "said": "say", "saw": "see", "seen": "see", "knew": "know",
    "known": "know", "gave": "give", "given": "give", "found": "find",
    "thought": "think", "came": "come", "sang": "sing", "sung": "sing",
    "sat": "sit", "won": "win", "spoke": "speak", "spoken": "speak",
    "broke": "break", "broken": "break", "chose": "choose",
    "chosen": "choose", "drove": "drive", "driven": "drive",
    "fell": "fall", "fallen": "fall", "flew": "fly", "flown": "fly",
    "grew": "grow", "grown": "grow", "threw": "throw", "thrown": "throw",
    "wore": "wear", "worn": "wear", "tore": "tear", "torn": "tear",
    "taught": "teach", "bought": "buy", "brought": "bring",
    "slept": "sleep", "felt": "feel", "left": "leave", "leaving": "leave",
    "stood": "stand", "understood": "understand",
    "began": "begin", "begun": "begin",
    "rose": "rise", "risen": "rise", "lay": "lie", "lain": "lie",
    "died": "die", "tied": "tie",
    # comparison
    "better": "good", "best": "good", "worse": "bad", "worst": "bad",
    # irregular plurals
    "children": "child", "men": "man", "women": "woman", "mice": "mouse",
    "geese": "goose", "teeth": "tooth", "feet": "foot", "people": "person",
    # exception-table demo: beats the "ies->y" rule which would give "cooky"
    "cookies": "cookie",
}


class LemmaEngine:
    """Maps inflected surface forms to a shared family key."""

    def __init__(self,
                 rules: Optional[List[Rule]] = None,
                 irregulars: Optional[Dict[str, str]] = None):
        self.rules: List[Rule] = list(DEFAULT_RULES if rules is None else rules)
        self.irregulars: Dict[str, str] = dict(
            DEFAULT_IRREGULARS if irregulars is None else irregulars)

    # -- updatable tables ---------------------------------------------------
    def add_irregular(self, form: str, lemma: str) -> None:
        """Add/override an exception entry. Exceptions beat all rules."""
        self.irregulars[form.lower()] = lemma.lower()

    def update_irregulars(self, entries: Dict[str, str]) -> None:
        for form, lemma in entries.items():
            self.add_irregular(form, lemma)

    def add_rule(self, rule: Rule, index: Optional[int] = None) -> None:
        """Insert a rule. Lower index = higher priority (first match wins)."""
        if index is None:
            self.rules.append(rule)
        else:
            self.rules.insert(index, rule)

    def remove_rule(self, name: str) -> bool:
        for i, rule in enumerate(self.rules):
            if rule.name == name:
                del self.rules[i]
                return True
        return False

    # -- core ---------------------------------------------------------------
    def lemmatize(self, word: str) -> str:
        if not word:
            return word
        # 1. possessive
        if len(word) > 2 and word.lower().endswith("'s"):
            word = word[:-2]
        # 3. non-English / non-alphabetic: pass through untouched
        if not word.isalpha() or not word.isascii():
            return word
        # 2. acronyms
        if word.isupper():
            return word
        if len(word) > 1 and word[-1] == "s" and word[:-1].isupper():
            return word[:-1]
        lower = word.lower()
        # 4. exception table beats every rule
        irregular = self.irregulars.get(lower)
        if irregular is not None:
            return irregular
        # 5. ordered rules, first match wins
        for rule in self.rules:
            result = rule.apply(lower)
            if result is not None:
                return result
        # 6. fallback
        return lower
