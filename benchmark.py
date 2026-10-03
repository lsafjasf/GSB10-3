"""Collect node-scale, timing and brute-force cross-check data.

Outputs CSV under data/ and prints summary tables.
Run: python3 benchmark.py
"""

import csv
import os
import random
import time

from sam import SuffixAutomaton
import brute

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def make_text(kind, n, rng):
    if kind == "same":
        return "a" * n
    if kind == "binary":
        return "".join(rng.choice("ab") for _ in range(n))
    if kind == "alpha4":
        return "".join(rng.choice("abcd") for _ in range(n))
    if kind == "alpha26":
        return "".join(chr(97 + rng.randrange(26)) for _ in range(n))
    if kind == "pattern":
        block = "abcdab"
        return (block * (n // len(block) + 1))[:n]
    raise ValueError(kind)


def timed_build(text):
    start = time.perf_counter()
    sam = SuffixAutomaton(text)
    build = time.perf_counter() - start
    start = time.perf_counter()
    distinct = sam.count_distinct_substrings()
    distinct_dt = time.perf_counter() - start
    start = time.perf_counter()
    most = sam.max_occurrence()
    occ_dt = time.perf_counter() - start
    return sam, build, distinct, distinct_dt, most, occ_dt


def node_scale():
    rng = random.Random(77)
    sizes = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
    kinds = ["same", "binary", "alpha4", "alpha26", "pattern"]
    rows = []
    for kind in kinds:
        for n in sizes:
            text = make_text(kind, n, rng)
            sam, build, distinct, _, most, _ = timed_build(text)
            upper = 1 if n == 0 else (2 if n == 1 else 2 * n - 1)
            rows.append({
                "kind": kind, "n": n, "states": sam.state_count,
                "states_over_n": f"{sam.state_count / n:.4f}" if n else "",
                "bound_2n_minus_1": upper,
                "within_bound": sam.state_count <= upper,
                "transitions": sam.transition_count(),
                "distinct_substrings": distinct,
                "max_occurrence": most,
                "build_ms": f"{build * 1000:.3f}",
            })
    path = os.path.join(DATA_DIR, "node_scale.csv")
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def crosscheck():
    rng = random.Random(88)
    cases = [
        ("single", lambda n: "a" * 0 if n == 0 else "a"),
        ("same", lambda n: "a" * n),
        ("binary", lambda n: "".join(rng.choice("ab") for _ in range(n))),
        ("alpha4", lambda n: "".join(rng.choice("abcd") for _ in range(n))),
        ("pattern", lambda n: ("abcabc" * (n // 6 + 1))[:n]),
    ]
    sizes = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 1000]
    rows = []
    for kind, factory in cases:
        for n in sizes:
            text = factory(n)
            sam = SuffixAutomaton(text)
            counts = brute.occurrence_counts(text)
            brute_distinct = len(counts)
            brute_most = max(counts.values(), default=0)
            sam_distinct = sam.count_distinct_substrings()
            sam_most = sam.max_occurrence()
            rows.append({
                "kind": kind, "n": n,
                "sam_states": sam.state_count,
                "brute_distinct": brute_distinct,
                "sam_distinct": sam_distinct,
                "distinct_match": brute_distinct == sam_distinct,
                "brute_max_occ": brute_most,
                "sam_max_occ": sam_most,
                "max_occ_match": brute_most == sam_most,
            })
    path = os.path.join(DATA_DIR, "crosscheck.csv")
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def print_table(rows, cols, limit_kinds=None):
    picked = rows if limit_kinds is None else [r for r in rows if r["kind"] in limit_kinds]
    print("  ".join(f"{c:>18}" for c in cols))
    for r in picked:
        print("  ".join(f"{str(r[c]):>18}" for c in cols))


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    print("== node scale (n vs states) ==")
    rows = node_scale()
    for kind in ["same", "binary", "alpha26", "pattern"]:
        print(f"-- {kind}")
        print_table(rows, ["n", "states", "states_over_n", "bound_2n_minus_1", "within_bound", "build_ms"], [kind])
        print()

    print("== brute-force cross-check ==")
    checks = crosscheck()
    ok = all(r["distinct_match"] and r["max_occ_match"] for r in checks)
    print(f"all {len(checks)} cases agree with brute force: {ok}")
    print_table(checks, ["kind", "n", "sam_states", "brute_distinct", "sam_distinct", "brute_max_occ", "sam_max_occ"])
    print(f"\nCSV written to {DATA_DIR}/")


if __name__ == "__main__":
    main()
