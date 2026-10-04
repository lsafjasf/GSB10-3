"""Self-tests for corpus maintenance.

Run: python3 -m unittest discover -s tests -v  (from repo root)

Edge cases required by the spec:
  * brand-new coverage gain      test_new_gain_is_admitted
  * exact duplicate              test_exact_duplicate_is_discarded
  * no-gain (already covered)    test_no_gain_input_is_discarded
  * capacity full + eviction     test_capacity_full_evicts_redundant_entry
  * capacity hard full           test_capacity_full_without_redundant_is_rejected
  * all inputs invalid           test_all_invalid_inputs
Plus minimization, admission-rate stats and eviction coverage comparison.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from corpus import Corpus, minimize
import target


class CorpusTests(unittest.TestCase):
    def test_new_gain_is_admitted(self):
        corpus = Corpus(max_size=10, coverage_fn=target.run)
        outcome = corpus.add(b"FUZZ\x00" + b"X" * 10)
        self.assertEqual(outcome, "added")
        self.assertEqual(corpus.size, 1)
        self.assertGreater(len(corpus.coverage), 0)
        self.assertEqual(corpus.stats.admission_rate, 1.0)

    def test_exact_duplicate_is_discarded(self):
        corpus = Corpus(max_size=10, coverage_fn=target.run)
        payload = b"FUZZ\x01AAAAAAA"
        self.assertEqual(corpus.add(payload), "added")
        self.assertEqual(corpus.add(payload), "duplicate")
        self.assertEqual(corpus.size, 1)
        self.assertEqual(corpus.stats.rejected_duplicate, 1)

    def test_no_gain_input_is_discarded(self):
        corpus = Corpus(max_size=10, coverage_fn=target.run)
        # First input covers edges {0,1,3,7,8,11}.
        self.assertEqual(corpus.add(b"FUZZ\x01" + b"A" * 10), "added")
        # Same edges with a different byte string: not a duplicate, no new edge.
        self.assertEqual(corpus.add(b"FUZZ\x01" + b"A" * 12), "no_gain")
        self.assertEqual(corpus.size, 1)
        self.assertEqual(corpus.stats.rejected_no_gain, 1)

    def test_capacity_full_evicts_redundant_entry(self):
        corpus = Corpus(max_size=2, coverage_fn=target.run)
        corpus.add(b"FUZZ\x00")        # edges {0,1,2}
        corpus.add(b"FUZZ\x01A")       # edges {0,1,3,7,11}
        self.assertEqual(corpus.size, 2)
        # New gain: edge 6 (NUL in payload). Its coverage {0,1,2,6} also
        # subsumes entry #1, making #1 fully redundant -> evicted.
        outcome = corpus.add(b"FUZZ\x00\x00")
        self.assertEqual(outcome, "evicted_and_added")
        self.assertEqual(corpus.size, 2)
        # Eviction policy: only fully-redundant entries are dropped, so the
        # union coverage after evict+insert must be a superset of before.
        report = corpus.eviction_reports[-1]
        self.assertEqual(report.coverage_lost, 0)
        self.assertGreaterEqual(report.coverage_after, report.coverage_before)
        self.assertEqual(report.corpus_size_before, 2)
        self.assertEqual(report.corpus_size_after, 2)
        self.assertIn(6, corpus.coverage)
        self.assertIn(2, corpus.coverage)  # kept via the new entry

    def test_capacity_full_without_redundant_is_rejected(self):
        # max_size 1: a single entry whose edges (0,1,2) cannot be fully
        # covered by a different-gain candidate, so nothing is evictable.
        corpus = Corpus(max_size=1, coverage_fn=target.run)
        self.assertEqual(corpus.add(b"FUZZ\x00"), "added")
        self.assertEqual(corpus.add(b"FUZZ\x01A"), "full")
        self.assertEqual(corpus.size, 1)
        self.assertEqual(corpus.stats.rejected_full, 1)
        self.assertEqual(len(corpus.eviction_reports), 0)

    def test_all_invalid_inputs(self):
        corpus = Corpus(max_size=10, coverage_fn=target.run)
        invalid_inputs = [b"", b"F", b"XXXX\x00abc", bytes(range(5)), b"fuzz\x00abc"]
        for data in invalid_inputs:
            self.assertEqual(corpus.add(data), "invalid")
        self.assertEqual(corpus.size, 0)
        self.assertEqual(len(corpus.coverage), 0)
        self.assertEqual(corpus.stats.rejected_invalid, len(invalid_inputs))
        self.assertEqual(corpus.stats.admission_rate, 0.0)
        self.assertEqual(len(corpus.eviction_reports), 0)

    def test_minimization_preserves_coverage_and_shrinks_size(self):
        original = b"FUZZ\x01A" + b"\x01" * 7  # 13 bytes, padding is useless
        original_cov = target.run(original)
        shrunk, shrunk_cov = minimize(original, target.run)
        self.assertLess(len(shrunk), len(original))
        self.assertTrue(original_cov <= shrunk_cov)
        # The deep edge 11 needs magic + type 1 + 'A', so 6 bytes survive.
        self.assertIn(11, shrunk_cov)
        self.assertEqual(shrunk, b"FUZZ\x01A")

    def test_minimization_keeps_coverage_mandated_bytes(self):
        # Edge 12 needs payload > 16 bytes, so at least 17 payload bytes
        # must survive minimization.
        original = b"FUZZ\x01" + b"A" * 30 + b"junk-junk-junk-junk"
        original_cov = target.run(original)
        self.assertIn(12, original_cov)
        shrunk, shrunk_cov = minimize(original, target.run)
        self.assertTrue(original_cov <= shrunk_cov)
        self.assertEqual(len(shrunk), 5 + 17)

    def test_admission_rate_stats(self):
        corpus = Corpus(max_size=10, coverage_fn=target.run)
        stream = [
            b"garbage",                  # invalid
            b"FUZZ\x00" + b"p" * 9,     # added (new gain)
            b"FUZZ\x00" + b"p" * 9,     # duplicate
            b"FUZZ\x00" + b"q" * 9,     # no_gain
            b"FUZZ\x01AAAAAAAA",        # added (new edges)
        ]
        for data in stream:
            corpus.add(data)
        self.assertEqual(corpus.stats.offered, 5)
        self.assertEqual(corpus.stats.accepted, 2)
        self.assertAlmostEqual(corpus.stats.admission_rate, 2 / 5)
        # Every accepted input was minimized before storage.
        for entry in corpus._entries.values():
            self.assertLessEqual(entry.stored_size, entry.original_size)


if __name__ == "__main__":
    unittest.main(verbosity=2)
