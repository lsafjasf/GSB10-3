"""登录态校验（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import MAX_LOGIN_ATTEMPTS, SESSION_TIMEOUT_MINUTES


def session_valid(issued_minutes_ago):
    return issued_minutes_ago < SESSION_TIMEOUT_MINUTES


def login_allowed(failed_attempts):
    return failed_attempts < MAX_LOGIN_ATTEMPTS

