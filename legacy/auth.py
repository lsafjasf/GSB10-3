"""登录态校验（重构前）。"""

SESSION_TIMEOUT_MINUTES = 30
MAX_LOGIN_ATTEMPTS = 5


def session_valid(issued_minutes_ago):
    return issued_minutes_ago < SESSION_TIMEOUT_MINUTES


def login_allowed(failed_attempts):
    return failed_attempts < MAX_LOGIN_ATTEMPTS

