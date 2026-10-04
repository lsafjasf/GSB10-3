"""Human-readable grading of ratios and budgets."""

from .thresholds import ALERT_THRESHOLD, REQUEST_TIMEOUT


def grade(ratio):
    if ratio >= ALERT_THRESHOLD:
        return "critical"
    if ratio >= ALERT_THRESHOLD / 2:
        return "warn"
    return "ok"


def format_timeout_budget():
    return "timeout_budget=%ds" % REQUEST_TIMEOUT
