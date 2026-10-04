"""接入层中间件（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import HTTP_REQUEST_TIMEOUT_SECONDS, RATE_LIMIT_PER_MINUTE


def check_request(requests_this_minute):
    if requests_this_minute >= RATE_LIMIT_PER_MINUTE:
        return {"allowed": False, "retry_after_seconds": 60}
    return {"allowed": True, "timeout_budget": HTTP_REQUEST_TIMEOUT_SECONDS}

