"""边界用例：针对重构后代码，在每个阈值的临界点上验证行为。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import api_client, auth, exporter, middleware, notifier  # noqa: E402
from app import pricing, report, scheduler, session, worker  # noqa: E402
from app.thresholds import (  # noqa: E402
    BULK_THRESHOLD,
    EXPORT_BATCH_SIZE,
    EXPORT_REQUEST_TIMEOUT_SECONDS,
    HTTP_REQUEST_TIMEOUT_SECONDS,
    INGEST_BATCH_SIZE,
    JOB_TIMEOUT_SECONDS,
    MAX_DISCOUNT,
    MAX_LOGIN_ATTEMPTS,
    MAX_RETRIES,
    RATE_LIMIT_PER_MINUTE,
    SESSION_TIMEOUT_MINUTES,
    SMS_LENGTH_LIMIT,
)


class RetryBoundary(unittest.TestCase):
    def test_last_retry_succeeds(self):
        result = worker.run_job(fail_times=MAX_RETRIES)
        self.assertEqual(result, {"ok": True, "attempts": MAX_RETRIES + 1})

    def test_retries_exhausted(self):
        result = worker.run_job(fail_times=MAX_RETRIES + 1)
        self.assertEqual(result, {"ok": False, "attempts": MAX_RETRIES + 1})

    def test_api_client_retry_boundary(self):
        ok = api_client.http_get("u", 0, fail_times=MAX_RETRIES)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["attempts"], MAX_RETRIES + 1)
        exhausted = api_client.http_get("u", 0, fail_times=MAX_RETRIES + 1)
        self.assertEqual(exhausted["error"], "exhausted")


class RateLimitBoundary(unittest.TestCase):
    def test_below_limit_allowed(self):
        self.assertTrue(middleware.check_request(RATE_LIMIT_PER_MINUTE - 1)["allowed"])

    def test_at_limit_rejected(self):
        result = middleware.check_request(RATE_LIMIT_PER_MINUTE)
        self.assertFalse(result["allowed"])

    def test_api_client_at_limit(self):
        result = api_client.http_get("u", RATE_LIMIT_PER_MINUTE)
        self.assertEqual(result["error"], "rate_limited")


class SessionBoundary(unittest.TestCase):
    def test_one_minute_before_expiry_valid(self):
        self.assertTrue(auth.session_valid(SESSION_TIMEOUT_MINUTES - 1))

    def test_at_expiry_invalid(self):
        self.assertFalse(auth.session_valid(SESSION_TIMEOUT_MINUTES))


class LoginBoundary(unittest.TestCase):
    def test_last_attempt_allowed(self):
        self.assertTrue(auth.login_allowed(MAX_LOGIN_ATTEMPTS - 1))

    def test_locked_at_limit(self):
        self.assertFalse(auth.login_allowed(MAX_LOGIN_ATTEMPTS))

    def test_lockout_remaining_zero_at_limit(self):
        self.assertEqual(session.lockout_remaining(MAX_LOGIN_ATTEMPTS), 0)


class DiscountBoundary(unittest.TestCase):
    def test_discount_at_cap_not_clamped(self):
        self.assertEqual(pricing.price(1, 10.0, MAX_DISCOUNT), 10.0 * (1 - MAX_DISCOUNT))

    def test_discount_above_cap_clamped(self):
        self.assertEqual(
            pricing.price(1, 10.0, MAX_DISCOUNT + 0.05),
            pricing.price(1, 10.0, MAX_DISCOUNT),
        )


class BulkBoundary(unittest.TestCase):
    def test_below_threshold_no_bulk_discount(self):
        self.assertEqual(pricing.price(BULK_THRESHOLD - 1, 10.0, 0.0), (BULK_THRESHOLD - 1) * 10.0)

    def test_at_threshold_bulk_discount_applied(self):
        self.assertEqual(
            pricing.price(BULK_THRESHOLD, 10.0, 0.0),
            round(BULK_THRESHOLD * 10.0 * 0.95, 2),
        )


class SmsBoundary(unittest.TestCase):
    def test_at_limit_sent(self):
        self.assertTrue(notifier.send_sms("x" * SMS_LENGTH_LIMIT)["sent"])

    def test_over_limit_rejected_with_segments(self):
        result = notifier.send_sms("x" * (SMS_LENGTH_LIMIT + 1))
        self.assertEqual(result["error"], "too_long")
        self.assertEqual(result["segments"], 2)


class BatchBoundary(unittest.TestCase):
    def test_ingest_exact_multiple_single_batch(self):
        self.assertEqual(scheduler.plan_batches(INGEST_BATCH_SIZE), [INGEST_BATCH_SIZE])

    def test_ingest_one_over_spills(self):
        self.assertEqual(
            scheduler.plan_batches(INGEST_BATCH_SIZE + 1),
            [INGEST_BATCH_SIZE, 1],
        )

    def test_export_exact_multiple_single_page(self):
        self.assertEqual(exporter.export_pages(EXPORT_BATCH_SIZE), [EXPORT_BATCH_SIZE])

    def test_export_one_over_spills(self):
        self.assertEqual(
            exporter.export_pages(EXPORT_BATCH_SIZE + 1),
            [EXPORT_BATCH_SIZE, 1],
        )

    def test_report_uses_ingest_batch_size(self):
        self.assertEqual(report.build_report(INGEST_BATCH_SIZE + 1)["chunks"],
                         [INGEST_BATCH_SIZE, 1])


class TimeoutBoundary(unittest.TestCase):
    def test_job_not_timed_out_at_limit(self):
        self.assertFalse(worker.job_timed_out(JOB_TIMEOUT_SECONDS))

    def test_job_timed_out_one_second_over(self):
        self.assertTrue(worker.job_timed_out(JOB_TIMEOUT_SECONDS + 1))

    def test_timeout_values(self):
        self.assertEqual(exporter.download_timeout(), EXPORT_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(scheduler.batch_timeout(), JOB_TIMEOUT_SECONDS)
        self.assertEqual(
            middleware.check_request(0)["timeout_budget"],
            HTTP_REQUEST_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()

