"""对外 API 客户端（重构前）。"""

REQUEST_TIMEOUT = 30        # 秒
MAX_RETRIES = 3             # 首次失败后的最大重试次数
RATE_LIMIT_PER_MINUTE = 100


def http_get(url, minute_requests, fail_times=0):
    """模拟一次 GET 请求。

    minute_requests: 当前自然分钟内已发出的请求数。
    fail_times: 前 fail_times 次尝试会失败（瞬时错误）。
    """
    if minute_requests >= RATE_LIMIT_PER_MINUTE:
        return {"ok": False, "error": "rate_limited", "attempts": 0,
                "timeout": REQUEST_TIMEOUT}
    attempts = 0
    while True:
        attempts += 1
        if attempts > fail_times:
            return {"ok": True, "status": 200, "attempts": attempts,
                    "timeout": REQUEST_TIMEOUT}
        if attempts > MAX_RETRIES:
            return {"ok": False, "error": "exhausted", "attempts": attempts,
                    "timeout": REQUEST_TIMEOUT}

