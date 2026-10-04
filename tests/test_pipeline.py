"""Post-refactor counterpart of legacy_tests/test_pipeline.py.

WARMUP_ROUNDS is purely test scaffolding, so it intentionally stays local
to the test package instead of entering app.thresholds.
"""

import unittest

from app import aggregator

WARMUP_ROUNDS = 2  # test-only constant; deliberately not in app.thresholds


class PipelineTest(unittest.TestCase):
    def test_windows_after_warmup(self):
        samples = [(i * 0.5, 1.0) for i in range(WARMUP_ROUNDS * 5)]
        windows = aggregator.aggregate(samples)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["count"], WARMUP_ROUNDS * 5)
