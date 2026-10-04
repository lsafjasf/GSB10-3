"""Ratio alerting with a cooldown."""

ALERT_THRESHOLD = 0.95
FLUSH_INTERVAL = 60  # seconds; alert cooldown


def breached(ratio):
    return ratio >= ALERT_THRESHOLD


def may_alert(last_alert_at, now):
    return last_alert_at is None or now - last_alert_at >= FLUSH_INTERVAL
