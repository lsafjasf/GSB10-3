"""Measure correct vs incorrect merges of the lemmatizer.

Definitions (pair-level, over all gold word families):
- positive pair : two forms of the SAME family
- negative pair : two forms of DIFFERENT families
- correct merge : positive pair that receives the same key   (true positive)
- missed merge  : positive pair that receives different keys (false negative)
- incorrect merge: negative pair that receives the same key  (over-stemming)

Two configurations are compared: the conservative default rule set and an
aggressive rule set that also strips derivational suffixes / final -e.
"""

from itertools import combinations
from typing import Dict, List, Tuple

from stemmer import AGGRESSIVE_EXTRA_RULES, DEFAULT_RULES, LemmaEngine
from word_families import FAMILIES


Pair = Tuple[str, str]


def evaluate(engine: LemmaEngine,
             families: Dict[str, List[str]]) -> Dict[str, object]:
    keys = {w: engine.lemmatize(w)
            for forms in families.values() for w in forms}
    family_of = {w: f for f, forms in families.items() for w in forms}

    positive: List[Pair] = []
    for forms in families.values():
        positive.extend(combinations(forms, 2))
    words = sorted(keys)
    negative: List[Pair] = [
        (a, b) for a, b in combinations(words, 2) if family_of[a] != family_of[b]
    ]

    correct = [(a, b) for a, b in positive if keys[a] == keys[b]]
    missed = [(a, b) for a, b in positive if keys[a] != keys[b]]
    incorrect = [(a, b) for a, b in negative if keys[a] == keys[b]]

    return {
        "families": len(families),
        "words": len(words),
        "positive_pairs": len(positive),
        "negative_pairs": len(negative),
        "correct": len(correct),
        "missed": len(missed),
        "incorrect": len(incorrect),
        "correct_pairs": correct,
        "missed_pairs": missed,
        "incorrect_pairs": incorrect,
        "keys": keys,
    }


def _pct(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator * 100:.2f}%" if denominator else "n/a"


def print_report(name: str, result: Dict[str, object], show_examples: int = 12) -> None:
    correct = result["correct"]
    incorrect = result["incorrect"]
    missed = result["missed"]
    print(f"== {name} ==")
    print(f"  families / words        : {result['families']} / {result['words']}")
    print(f"  positive pairs (same)   : {result['positive_pairs']}")
    print(f"  negative pairs (differ.): {result['negative_pairs']}")
    print(f"  correct merges          : {correct}")
    print(f"  missed merges           : {missed}")
    print(f"  incorrect merges        : {incorrect}")
    print(f"  merge precision         : {_pct(correct, correct + incorrect)}")
    print(f"  merge recall            : {_pct(correct, correct + missed)}")
    bad = result["incorrect_pairs"]
    if bad:
        shown = bad[:show_examples]
        print("  over-merge examples (word -> shared key):")
        keys = result["keys"]
        for a, b in shown:
            print(f"    {a} ~ {b} -> {keys[a]}")
    if missed:
        print("  missed-merge examples:")
        for a, b in result["missed_pairs"][:show_examples]:
            print(f"    {a} ({result['keys'][a]}) != {b} ({result['keys'][b]})")
    print()


def main() -> None:
    default_engine = LemmaEngine()
    aggressive_engine = LemmaEngine(rules=DEFAULT_RULES + AGGRESSIVE_EXTRA_RULES)

    for name, engine in (("default rules", default_engine),
                         ("aggressive rules (+ity/-ness/-ment/-ous/-al/-e)",
                          aggressive_engine)):
        print_report(name, evaluate(engine, FAMILIES))


if __name__ == "__main__":
    main()
