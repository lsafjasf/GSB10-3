"""Single registry for every pipeline threshold.

Adoption rule (see docs/thresholds.md): the adopted value is the value on
the *live* code path in the legacy baseline. Values that existed only in
dead / never-imported copies were dropped, so wiring modules to this
registry does not change any externally observable behavior.

Test-only tuning (e.g. WARMUP_ROUNDS) stays inside the test package; it is
deliberately not part of this registry.
"""

REQUEST_TIMEOUT = 30  # seconds; overall per-request budget
MAX_RETRIES = 3
CACHE_TTL = 300  # seconds; default cache entry lifetime
BATCH_SIZE = 100
FLUSH_INTERVAL = 60  # seconds; aggregation window span / alert cooldown
ALERT_THRESHOLD = 0.95

_REGISTRY_TABLE = {
    "REQUEST_TIMEOUT": {
        "value": REQUEST_TIMEOUT,
        "adopted_from": ("legacy/client.py", "legacy/api.py", "legacy/report.py"),
        "dropped": (),
    },
    "MAX_RETRIES": {
        "value": MAX_RETRIES,
        "adopted_from": ("legacy/client.py",),
        "dropped": (("legacy/worker.py", 5), ("legacy/settings.py", 3)),
    },
    "CACHE_TTL": {
        "value": CACHE_TTL,
        "adopted_from": ("legacy/cache.py",),
        "dropped": (("legacy/api.py", 360), ("legacy/settings.py", 300)),
    },
    "BATCH_SIZE": {
        "value": BATCH_SIZE,
        "adopted_from": ("legacy/aggregator.py", "legacy/worker.py"),
        "dropped": (("legacy/settings.py", 100),),
    },
    "FLUSH_INTERVAL": {
        "value": FLUSH_INTERVAL,
        "adopted_from": ("legacy/aggregator.py", "legacy/alerting.py"),
        "dropped": (("legacy/settings.py", 60),),
    },
    "ALERT_THRESHOLD": {
        "value": ALERT_THRESHOLD,
        "adopted_from": ("legacy/alerting.py", "legacy/report.py"),
        "dropped": (
            ("legacy/settings.py", 0.90),
            ("legacy_tests/test_alert.py", 0.95),
        ),
    },
}

import types as _types

REGISTRY = _types.MappingProxyType(_REGISTRY_TABLE)

__all__ = (
    "REQUEST_TIMEOUT",
    "MAX_RETRIES",
    "CACHE_TTL",
    "BATCH_SIZE",
    "FLUSH_INTERVAL",
    "ALERT_THRESHOLD",
    "REGISTRY",
)
