"""Request handler glued over client + cache."""

from . import client
from .thresholds import REQUEST_TIMEOUT


def handle(fetcher, elapsed, store):
    if elapsed() >= REQUEST_TIMEOUT:
        return {"status": "rejected", "reason": "timeout-budget-exhausted"}
    result = client.fetch_with_retry(fetcher, elapsed)
    if result["status"] == "ok":
        store.put("last_value", result["value"], ttl=120)
    return result
