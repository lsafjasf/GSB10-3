"""Ratio alerting with a cooldown."""

from .thresholds import ALERT_THRESHOLD, FLUSH_INTERVAL


def breached(ratio):
    return ratio >= ALERT_THRESHOLD


def may_alert(last_alert_at, now):
    return last_alert_at is None or now - last_alert_at >= FLUSH_INTERVAL
