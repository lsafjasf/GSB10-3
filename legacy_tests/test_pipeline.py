import unittest

from legacy import aggregator

WARMUP_ROUNDS = 2  # test-only: warmup passes before measuring


class PipelineTest(unittest.TestCase):
    def test_windows_after_warmup(self):
        samples = [(float(i), 1.0) for i in range(WARMUP_ROUNDS * 5)]
        windows = aggregator.aggregate(samples)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["count"], WARMUP_ROUNDS * 5)
