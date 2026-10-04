"""Boundary-value tests at each adopted threshold.

Every case runs against BOTH the legacy baseline and the refactored
package, so the suite pins the exact pre-refactor boundary semantics
(>= vs >, inclusive vs exclusive) and proves the refactor preserves them.
"""

import unittest

import app.aggregator
import app.alerting
import app.cache
import app.client
import app.report
import app.worker
import legacy.aggregator
import legacy.alerting
import legacy.cache
import legacy.client
import legacy.report
import legacy.worker

from tests.fixtures import (
    always_failing_fetcher,
    fake_clock,
    flaky_fetcher,
    ok_fetcher,
    scripted_elapsed,
)

EPSILON = 1e-9

MODULE_PAIRS = (
    ("legacy", legacy),
    ("app", app),
)


class TimeoutBoundaryTest(unittest.TestCase):
    def test_exactly_at_timeout(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                result = pkg.client.fetch_with_retry(
                    ok_fetcher(), scripted_elapsed([30.0])
                )
                self.assertEqual(result, {"status": "timeout", "attempts": 0})

    def test_just_under_timeout(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                result = pkg.client.fetch_with_retry(
                    ok_fetcher("w"), scripted_elapsed([30.0 - EPSILON])
                )
                self.assertEqual(
                    result, {"status": "ok", "value": "w", "attempts": 1}
                )


class RetryBoundaryTest(unittest.TestCase):
    def test_fails_exactly_max_retries_times(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                result = pkg.client.fetch_with_retry(
                    always_failing_fetcher(pkg.client.TransientError),
                    scripted_elapsed([0.0, 1.0, 2.0, 3.0]),
                )
                self.assertEqual(result, {"status": "failed", "attempts": 3})

    def test_succeeds_on_last_allowed_attempt(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                result = pkg.client.fetch_with_retry(
                    flaky_fetcher(2, pkg.client.TransientError, "last"),
                    scripted_elapsed([0.0, 1.0, 2.0]),
                )
                self.assertEqual(
                    result, {"status": "ok", "value": "last", "attempts": 3}
                )


class CacheTtlBoundaryTest(unittest.TestCase):
    def test_get_at_exact_expiry_is_a_miss(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                clock = fake_clock(0.0)
                store = pkg.cache.Cache(clock)
                store.put("k", "v")
                clock.set(300.0)
                self.assertIsNone(store.get("k"))
                self.assertEqual(len(store), 0)

    def test_get_just_before_expiry_is_a_hit(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                clock = fake_clock(0.0)
                store = pkg.cache.Cache(clock)
                store.put("k", "v")
                clock.set(300.0 - EPSILON)
                self.assertEqual(store.get("k"), "v")

    def test_explicit_ttl_overrides_default(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                clock = fake_clock(0.0)
                store = pkg.cache.Cache(clock)
                store.put("k", "v", ttl=120)
                clock.set(120.0)
                self.assertIsNone(store.get("k"))


class BatchBoundaryTest(unittest.TestCase):
    def test_exact_batch_size_flushes_once(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                batches = []
                flushes = pkg.worker.drain(list(range(100)), batches.append)
                self.assertEqual(flushes, 1)
                self.assertEqual(len(batches[0]), 100)

    def test_batch_size_plus_one_flushes_twice(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                batches = []
                flushes = pkg.worker.drain(list(range(101)), batches.append)
                self.assertEqual(flushes, 2)
                self.assertEqual([len(b) for b in batches], [100, 1])

    def test_empty_input_flushes_never(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                self.assertEqual(pkg.worker.drain([], lambda b: None), 0)


class WindowBoundaryTest(unittest.TestCase):
    def test_gap_exactly_flush_interval_opens_new_window(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                windows = pkg.aggregator.aggregate([(0.0, 1.0), (60.0, 2.0)])
                self.assertEqual(len(windows), 2)
                self.assertEqual(windows[0]["count"], 1)

    def test_gap_just_under_flush_interval_stays_in_window(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                windows = pkg.aggregator.aggregate(
                    [(0.0, 1.0), (60.0 - EPSILON, 2.0)]
                )
                self.assertEqual(len(windows), 1)
                self.assertEqual(windows[0]["count"], 2)

    def test_exactly_batch_size_samples_stay_in_one_window(self):
        # 100 samples inside a single 60s span: count boundary, not time.
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                samples = [(i * 0.5, 1.0) for i in range(100)]
                windows = pkg.aggregator.aggregate(samples)
                self.assertEqual(len(windows), 1)
                self.assertEqual(windows[0]["count"], 100)

    def test_batch_size_plus_one_samples_open_second_window(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                samples = [(i * 0.5, 1.0) for i in range(101)]
                windows = pkg.aggregator.aggregate(samples)
                self.assertEqual(len(windows), 2)
                self.assertEqual(
                    [w["count"] for w in windows], [100, 1]
                )


class AlertBoundaryTest(unittest.TestCase):
    def test_ratio_exactly_at_threshold_breaches(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                self.assertTrue(pkg.alerting.breached(0.95))

    def test_ratio_just_under_threshold_does_not_breach(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                self.assertFalse(pkg.alerting.breached(0.95 - EPSILON))

    def test_cooldown_exactly_elapsed_allows_alert(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                self.assertTrue(pkg.alerting.may_alert(0.0, 60.0))
                self.assertFalse(pkg.alerting.may_alert(0.0, 60.0 - EPSILON))

    def test_grade_boundaries(self):
        for label, pkg in MODULE_PAIRS:
            with self.subTest(package=label):
                self.assertEqual(pkg.report.grade(0.95), "critical")
                self.assertEqual(pkg.report.grade(0.95 - EPSILON), "warn")
                self.assertEqual(pkg.report.grade(0.475), "warn")
                self.assertEqual(pkg.report.grade(0.475 - EPSILON), "ok")


if __name__ == "__main__":
    unittest.main()
