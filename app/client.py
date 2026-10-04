"""Fetch with a retry budget and an overall timeout."""

from .thresholds import MAX_RETRIES, REQUEST_TIMEOUT


class TransientError(Exception):
    """Recoverable fetch failure."""


def fetch_with_retry(fetcher, elapsed):
    """Call fetcher(attempt) until it succeeds, the retry budget is
    exhausted, or REQUEST_TIMEOUT seconds have elapsed.

    fetcher: callable(attempt_index) -> value, may raise TransientError
    elapsed: callable() -> seconds since the request started
    """
    attempts = 0
    while True:
        if elapsed() >= REQUEST_TIMEOUT:
            return {"status": "timeout", "attempts": attempts}
        try:
            value = fetcher(attempts)
        except TransientError:
            attempts += 1
            if attempts >= MAX_RETRIES:
                return {"status": "failed", "attempts": attempts}
        else:
            return {"status": "ok", "value": value, "attempts": attempts + 1}
