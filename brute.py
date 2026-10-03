"""Brute-force reference implementations used to cross-check the SAM."""

from collections import Counter


def distinct_substrings(text):
    return {text[i:j] for i in range(len(text)) for j in range(i + 1, len(text) + 1)}


def occurrence_counts(text):
    counts = Counter()
    n = len(text)
    for i in range(n):
        for j in range(i + 1, n + 1):
            counts[text[i:j]] += 1
    return counts


def count_occurrences(text, pattern):
    if not pattern:
        return 0
    total = 0
    start = 0
    while True:
        pos = text.find(pattern, start)
        if pos == -1:
            return total
        total += 1
        start = pos + 1


def longest_common_substring(a, b):
    best = ""
    for i in range(len(a)):
        for j in range(i + 1, len(a) + 1):
            piece = a[i:j]
            if len(piece) > len(best) and piece in b:
                best = piece
    return best


def max_occurrence(text):
    counts = occurrence_counts(text)
    return max(counts.values(), default=0)
