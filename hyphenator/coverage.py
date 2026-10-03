"""Generate rule-coverage data by hyphenating the bundled corpus.

Usage: python3 -m hyphenator.coverage
Writes coverage_report.json at the repository root.
"""
import json
from pathlib import Path

from .core import CORPUS_PATH, Hyphenator

ROOT = Path(__file__).resolve().parent.parent


def build_report(corpus_path=None, rules_dir=None):
    corpus = json.loads(Path(corpus_path or CORPUS_PATH).read_text(encoding="utf-8"))
    languages = {}
    for lang, words in corpus.items():
        h = Hyphenator(lang, rules_dir=rules_dir)
        examples = {}
        for word in words:
            examples[word] = h.hyphenate(word, hyphen="-")
        rule_coverage = {rid: len(ws) for rid, ws in h.coverage.items()}
        languages[lang] = {
            "cases": len(words),
            "cases_with_breaks": sum(1 for w in words if h.breakpoints(w)),
            "rule_coverage": rule_coverage,
            "examples": examples,
        }
    return {"languages": languages}


def main():
    report = build_report()
    out = ROOT / "coverage_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for lang, data in report["languages"].items():
        print(
            "%s: %d cases, %d with breaks"
            % (lang, data["cases"], data["cases_with_breaks"])
        )
        for rid, count in sorted(data["rule_coverage"].items()):
            print("    %-20s covers %d case(s)" % (rid, count))
    print("wrote", out)


if __name__ == "__main__":
    main()
