"""Rule coverage report: how many shipped cases each rule table covers.

Writes ``coverage.json`` next to the rule tables and prints a summary.
Exits non-zero if any case fails.

    python3 -m hyphenation.coverage
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from .core import Hyphenator, RULES_DIR
except ImportError:
    from core import Hyphenator, RULES_DIR

LANGUAGES = ["en", "de", "zh", "ja", "ko"]
OUT_PATH = Path(__file__).resolve().parent / "coverage.json"


def collect_coverage() -> dict:
    report = {}
    for lang in LANGUAGES:
        hyph = Hyphenator(lang)
        covered, failed = 0, []
        for case in hyph.rules.cases:
            text = case.get("word", case.get("text", ""))
            if hyph.break_points(text) == case["breaks"]:
                covered += 1
            else:
                failed.append(text)
        report[lang] = {
            **hyph.rules.to_meta(),
            "cases_total": len(hyph.rules.cases),
            "cases_covered": covered,
            "failed_cases": failed,
        }
    return report


def main() -> int:
    report = collect_coverage()
    OUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total_covered = total_cases = 0
    for lang, stats in report.items():
        total_covered += stats["cases_covered"]
        total_cases += stats["cases_total"]
        print(
            f"{lang} ({stats['language']:<9}): "
            f"{stats['cases_covered']}/{stats['cases_total']} cases covered, "
            f"patterns={stats['patterns']}, morphemes={stats['morphemes']}, "
            f"kinsoku={stats['kinsoku']}"
        )
        for text in stats["failed_cases"]:
            print(f"    FAIL: {text!r}")
    print(f"total: {total_covered}/{total_cases} cases covered")
    print(f"report written to {OUT_PATH}")
    return 0 if total_covered == total_cases else 1


if __name__ == "__main__":
    sys.exit(main())
