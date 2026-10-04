"""Human-readable grading of ratios and budgets."""

REQUEST_TIMEOUT = 30
ALERT_THRESHOLD = 0.95


def grade(ratio):
    if ratio >= ALERT_THRESHOLD:
        return "critical"
    if ratio >= ALERT_THRESHOLD / 2:
        return "warn"
    return "ok"


def format_timeout_budget():
    return "timeout_budget=%ds" % REQUEST_TIMEOUT
