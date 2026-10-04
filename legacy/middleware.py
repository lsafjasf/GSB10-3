"""接入层中间件（重构前）。"""

RATE_LIMIT_PER_MINUTE = 100
REQUEST_TIMEOUT = 30


def check_request(requests_this_minute):
    if requests_this_minute >= RATE_LIMIT_PER_MINUTE:
        return {"allowed": False, "retry_after_seconds": 60}
    return {"allowed": True, "timeout_budget": REQUEST_TIMEOUT}

