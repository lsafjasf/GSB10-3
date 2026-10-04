"""Simulated fuzzing campaign against the toy target.

Phase 1: 400 mixed inputs (garbage / duplicates / mutations / extensions)
         into a bounded corpus -- admission rate and minimization data.
Phase 2: a crafted sequence on a small corpus -- capacity-full eviction,
         the before/after coverage comparison, and the hard-full rejection.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from corpus import Corpus
import target

SEEDS = [
    b"FUZZ\x00" + b"\x00" * 40 + b"padding-padding-padding",
    b"FUZZ\x01" + b"A" * 30 + b"more-filler-bytes-here",
    b"FUZZ\x02" + b"de" + b"x" * 25,
    b"FUZZ\x03" + b"deadbeef" + b"adbeef" + b"y" * 20,
]


def mutate(rng: random.Random, seed: bytes) -> bytes:
    data = bytearray(seed)
    for _ in range(rng.randint(1, 4)):
        pos = rng.randrange(len(data))
        data[pos] = rng.randrange(256)
    return bytes(data)


def phase_one() -> None:
    rng = random.Random(20261004)
    corpus = Corpus(max_size=6, coverage_fn=target.run)
    outcomes = {}
    for _ in range(400):
        roll = rng.random()
        if roll < 0.25:
            candidate = bytes(rng.randrange(256) for _ in range(rng.randint(0, 12)))
        elif roll < 0.55:
            candidate = rng.choice(SEEDS)
        elif roll < 0.85:
            candidate = mutate(rng, rng.choice(SEEDS))
        else:
            seed = rng.choice(SEEDS)
            candidate = seed + bytes(rng.randrange(256) for _ in range(rng.randint(1, 6)))
        outcome = corpus.add(candidate)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    print("=== Phase 1: 400-input campaign, capacity=6 ===")
    for name in ("added", "evicted_and_added", "duplicate", "no_gain", "invalid", "full"):
        print(f"  {name:18s} {outcomes.get(name, 0)}")
    print()
    print(corpus.report())
    print()
    print("stored inputs (offered size -> minimized size):")
    for entry in sorted(corpus._entries.values(), key=lambda e: e.seq):
        print(
            f"  seq={entry.seq}: {entry.original_size:3d}B -> {entry.stored_size:3d}B "
            f"edges={sorted(entry.edges)}"
        )


def phase_two() -> None:
    print()
    print("=== Phase 2: capacity=3, crafted sequence ===")
    corpus = Corpus(max_size=3, coverage_fn=target.run)

    plan = [
        (b"FUZZ\x01A", "short type-1 input"),
        (b"FUZZ\x00\x00", "type-0 input with NUL"),
        (b"FUZZ\x02de", "type-2 input, payload 'de'"),
        (b"FUZZ\x01A" + b"B" * 20, "long type-1 input (new edges 8,12; subsumes #1)"),
        (b"FUZZ\x03adbeef", "type-3 input with 'adbeef' (new edges 5,10)"),
    ]
    for data, description in plan:
        outcome = corpus.add(data)
        print(f"  offer {len(data):3d}B | {description:55s} -> {outcome}")

    print()
    print("eviction coverage comparison:")
    for report in corpus.eviction_reports:
        print("  " + report.describe())
    print()
    print(corpus.report())
    print()
    print("stored inputs (offered -> minimized):")
    for entry in sorted(corpus._entries.values(), key=lambda e: e.seq):
        print(
            f"  seq={entry.seq}: {entry.original_size:3d}B -> {entry.stored_size:3d}B "
            f"edges={sorted(entry.edges)}"
        )


if __name__ == "__main__":
    phase_one()
    phase_two()
