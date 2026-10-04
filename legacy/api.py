"""Request handler glued over client + cache."""

from . import client

REQUEST_TIMEOUT = 30  # seconds; handler-level reject budget
CACHE_TTL = 360  # dead: handlers always pass an explicit ttl, never read this


def handle(fetcher, elapsed, store):
    if elapsed() >= REQUEST_TIMEOUT:
        return {"status": "rejected", "reason": "timeout-budget-exhausted"}
    result = client.fetch_with_retry(fetcher, elapsed)
    if result["status"] == "ok":
        store.put("last_value", result["value"], ttl=120)
    return result
