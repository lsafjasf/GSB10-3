"""Demo: differential testing + minimization, with full process data.

Two implementations of summation disagree once the total exceeds 32-bit
signed int range. The demo finds a disagreement on the raw input, minimizes
it to a smallest counterexample, prints every reduction round with its
before/after difference assertion, and writes machine-readable process data
to examples/minimization_trace.json.

Run from the repository root:
    python3 examples/demo.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffmin import minimize_differential  # noqa: E402


def sum_python(values):
    return sum(values)


def sum_int32(values):
    total = 0
    for value in values:
        total = (total + value) & 0xFFFFFFFF
    return total - (1 << 32) if total >= (1 << 31) else total


def main():
    original = [3, 1, 4, 2**31 - 1, 9, 2, 7, 0, 5, 8, 6, 10]
    print("original raw input:", original)

    result, difference = minimize_differential(sum_python, sum_int32, original)

    print("\nminimal counterexample:", result.minimized)
    print("final difference:")
    print("  " + str(difference).replace("\n", "\n  "))

    print("\nper-round before/after difference assertions:")
    header = f"{'iter':>4} {'strategy':>8} {'before':>6} {'after':>5} {'diff_before':>11} {'diff_after':>10} accepted detail"
    print(header)
    for r in result.rounds:
        print(
            f"{r.iteration:>4} {r.strategy:>8} {r.before_size:>6} {r.after_size:>5} "
            f"{str(r.before_diff):>11} {str(r.after_diff):>10} {str(r.accepted):>7}  {r.detail}"
        )

    summary = {
        "initial_size": result.initial_size,
        "final_size": result.final_size,
        "total_iterations": result.iterations,
        "accepted_iterations": len(result.accepted_rounds()),
        "rejected_iterations": len(result.rejected_rounds()),
        "difference_checks": result.difference_checks,
        "is_minimal_verified": result.is_minimal_verified,
    }
    print("\nsummary:", json.dumps(summary, ensure_ascii=False))

    trace_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "minimization_trace.json")
    with open(trace_path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, ensure_ascii=False, indent=2)
    print("process data written to:", trace_path)


if __name__ == "__main__":
    main()
