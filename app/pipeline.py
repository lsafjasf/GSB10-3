"""One deterministic pass through every subsystem."""

from . import aggregator, alerting, cache, client, report, worker


def run_pipeline(samples, ratios, fetcher, clock, elapsed, last_alert_at, now):
    windows = aggregator.aggregate(samples)
    fetched = client.fetch_with_retry(fetcher, elapsed)
    store = cache.Cache(clock)
    if fetched["status"] == "ok":
        store.put("latest", fetched["value"])
    alerts = []
    for ratio in ratios:
        if alerting.breached(ratio) and alerting.may_alert(last_alert_at, now):
            alerts.append(report.grade(ratio))
    batches = []
    flushes = worker.drain([w["total"] for w in windows], batches.append)
    return {
        "windows": windows,
        "fetched": fetched,
        "cached": store.get("latest"),
        "cache_size": len(store),
        "alerts": alerts,
        "flushes": flushes,
        "batches": batches,
        "budget": report.format_timeout_budget(),
    }
