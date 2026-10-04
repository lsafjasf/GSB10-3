"""短信通知（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import MAX_RETRIES, SMS_LENGTH_LIMIT


def send_sms(message, fail_times=0):
    if len(message) > SMS_LENGTH_LIMIT:
        return {"sent": False, "error": "too_long",
                "segments": -(-len(message) // SMS_LENGTH_LIMIT)}
    attempts = 0
    while True:
        attempts += 1
        if attempts > fail_times:
            return {"sent": True, "attempts": attempts}
        if attempts > MAX_RETRIES:
            return {"sent": False, "error": "exhausted", "attempts": attempts}

