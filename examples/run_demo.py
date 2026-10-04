"""Reproducible demo: differential testing + minimization process data.

Run from the repository root:

    python3 examples/run_demo.py

It writes one JSON report per scenario into ``reports/`` and prints a
summary table plus the before/after assertion of every reduction round.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffmin import DifferentialHarness, minimize, NoDifferenceError

REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports"
)


def reference(xs):
    return sum(xs)


def bug_skip_first(xs):
    total = 0
    for index, value in enumerate(xs):
        if index == 0:
            continue
        total += value
    return total


def bug_tail(xs):
    total = sum(xs)
    if len(xs) > 0 and xs[-1] == 99:
        total += 1000
    return total


def bug_pair(xs):
    total = sum(xs)
    if 1 in xs and 2 in xs:
        total += 100
    return total


def bug_empty(xs):
    if len(xs) == 0:
        raise ValueError("empty input not supported")
    return sum(xs)


SCENARIOS = [
    ("already_minimal", bug_skip_first, [7]),
    ("only_half_removable", bug_pair, [1, 2, 3, 4]),
    ("difference_only_at_tail", bug_tail, list(range(50)) + [99]),
    ("empty_input_diverges", bug_empty, []),
]


def print_round_assertions(name, report):
    print("\n=== {}: before/after assertion per reduction round ===".format(name))
    for rnd in report.rounds:
        print(
            "round {} [{} n={}] removed={} ({} -> {})".format(
                rnd.index,
                rnd.phase,
                rnd.granularity,
                rnd.removed_count,
                len(rnd.before.input),
                len(rnd.after.input),
            )
        )
        for line in rnd.before.assertion_lines():
            print("  BEFORE " + line)
        for line in rnd.after.assertion_lines():
            print("  AFTER  " + line)
    print("FINAL: " + report.final_assertion.describe())
    print(
        "FINAL SWEEP ({} singleton check(s)):".format(
            len(report.singleton_checks)
        )
    )
    for check in report.singleton_checks:
        print("  index={} element={!r} kept={} ({})".format(
            check.index, check.element, check.kept, check.detail
        ))


def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    summary = []
    for name, candidate, original in SCENARIOS:
        harness = DifferentialHarness(reference, candidate)
        report = minimize(harness, original)
        path = os.path.join(REPORTS_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(report.to_json())
        print_round_assertions(name, report)
        summary.append(
            {
                "scenario": name,
                "original_size": report.original_size,
                "final_size": report.final_size,
                "iterations": report.iterations,
                "trials": report.trials,
                "rounds": len(report.rounds),
                "verified": report.verified,
                "report": os.path.relpath(path),
            }
        )

    honest = DifferentialHarness(reference, reference)
    try:
        minimize(honest, [1, 2, 3])
    except NoDifferenceError as exc:
        print("\nno-divergence guard: {}".format(exc))

    summary_path = os.path.join(REPORTS_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    print("\nscenario                  orig final iter trials rounds verified")
    for row in summary:
        print(
            "{:<25} {:>4} {:>5} {:>4} {:>6} {:>6} {}".format(
                row["scenario"],
                row["original_size"],
                row["final_size"],
                row["iterations"],
                row["trials"],
                row["rounds"],
                row["verified"],
            )
        )
    print("\nJSON reports written to {}/".format(REPORTS_DIR))


if __name__ == "__main__":
    main()
