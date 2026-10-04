"""阈值单一注册表（重构后唯一权威定义）。

重构前这些阈值散落在 11 个文件中，定义位置、取值、冲突处理与前后对照
见 docs/THRESHOLDS.md。新增或修改阈值只能改这里。
"""

HTTP_REQUEST_TIMEOUT_SECONDS = 30
EXPORT_REQUEST_TIMEOUT_SECONDS = 45
MAX_RETRIES = 3
RATE_LIMIT_PER_MINUTE = 100
JOB_TIMEOUT_SECONDS = 300
INGEST_BATCH_SIZE = 500
EXPORT_BATCH_SIZE = 1000
SESSION_TIMEOUT_MINUTES = 30
MAX_LOGIN_ATTEMPTS = 5
MAX_DISCOUNT = 0.30
BULK_THRESHOLD = 20
SMS_LENGTH_LIMIT = 160


def _entry(value, unit, description, former_locations):
    return {
        "value": value,
        "unit": unit,
        "description": description,
        "former_locations": former_locations,
    }


REGISTRY = {
    "HTTP_REQUEST_TIMEOUT_SECONDS": _entry(
        HTTP_REQUEST_TIMEOUT_SECONDS, "seconds", "普通 HTTP 请求超时",
        ["api_client.REQUEST_TIMEOUT", "middleware.REQUEST_TIMEOUT",
         "report.REQUEST_TIMEOUT"],
    ),
    "EXPORT_REQUEST_TIMEOUT_SECONDS": _entry(
        EXPORT_REQUEST_TIMEOUT_SECONDS, "seconds",
        "导出下载超时（冲突 C1 拆分：原 exporter.REQUEST_TIMEOUT=45）",
        ["exporter.REQUEST_TIMEOUT"],
    ),
    "MAX_RETRIES": _entry(
        MAX_RETRIES, "count", "首次失败后的最大重试次数",
        ["api_client.MAX_RETRIES", "worker.MAX_RETRIES",
         "notifier.MAX_RETRIES"],
    ),
    "RATE_LIMIT_PER_MINUTE": _entry(
        RATE_LIMIT_PER_MINUTE, "requests/minute", "每客户端每分钟限流",
        ["api_client.RATE_LIMIT_PER_MINUTE",
         "middleware.RATE_LIMIT_PER_MINUTE"],
    ),
    "JOB_TIMEOUT_SECONDS": _entry(
        JOB_TIMEOUT_SECONDS, "seconds", "后台任务超时",
        ["worker.JOB_TIMEOUT_SECONDS", "scheduler.JOB_TIMEOUT_SECONDS"],
    ),
    "INGEST_BATCH_SIZE": _entry(
        INGEST_BATCH_SIZE, "rows", "入库/报表分批大小",
        ["scheduler.MAX_BATCH_SIZE", "report.MAX_BATCH_SIZE"],
    ),
    "EXPORT_BATCH_SIZE": _entry(
        EXPORT_BATCH_SIZE, "rows",
        "导出分页大小（冲突 C2 拆分：原 exporter.MAX_BATCH_SIZE=1000）",
        ["exporter.MAX_BATCH_SIZE"],
    ),
    "SESSION_TIMEOUT_MINUTES": _entry(
        SESSION_TIMEOUT_MINUTES, "minutes",
        "会话令牌有效期（冲突 C3：采用 auth.py 活值 30；"
        "session.py 的 60 为死代码，已删除）",
        ["auth.SESSION_TIMEOUT_MINUTES"],
    ),
    "MAX_LOGIN_ATTEMPTS": _entry(
        MAX_LOGIN_ATTEMPTS, "count", "最大连续登录失败次数",
        ["auth.MAX_LOGIN_ATTEMPTS", "session.MAX_LOGIN_ATTEMPTS"],
    ),
    "MAX_DISCOUNT": _entry(
        MAX_DISCOUNT, "ratio", "折扣上限",
        ["pricing.MAX_DISCOUNT", "cart.MAX_DISCOUNT",
         "test_helpers.MAX_DISCOUNT"],
    ),
    "BULK_THRESHOLD": _entry(
        BULK_THRESHOLD, "items", "批量优惠起订量",
        ["pricing.BULK_THRESHOLD", "cart.BULK_THRESHOLD"],
    ),
    "SMS_LENGTH_LIMIT": _entry(
        SMS_LENGTH_LIMIT, "chars", "单条短信长度上限",
        ["notifier.SMS_LENGTH_LIMIT", "test_helpers.SMS_LENGTH_LIMIT"],
    ),
}


def validate():
    """注册表不变量校验，供启动自检与测试调用。"""
    checks = [
        (HTTP_REQUEST_TIMEOUT_SECONDS > 0,
         "HTTP_REQUEST_TIMEOUT_SECONDS 必须为正"),
        (EXPORT_REQUEST_TIMEOUT_SECONDS > 0,
         "EXPORT_REQUEST_TIMEOUT_SECONDS 必须为正"),
        (MAX_RETRIES >= 0, "MAX_RETRIES 不能为负"),
        (RATE_LIMIT_PER_MINUTE > 0, "RATE_LIMIT_PER_MINUTE 必须为正"),
        (JOB_TIMEOUT_SECONDS > 0, "JOB_TIMEOUT_SECONDS 必须为正"),
        (INGEST_BATCH_SIZE > 0, "INGEST_BATCH_SIZE 必须为正"),
        (EXPORT_BATCH_SIZE > 0, "EXPORT_BATCH_SIZE 必须为正"),
        (SESSION_TIMEOUT_MINUTES > 0, "SESSION_TIMEOUT_MINUTES 必须为正"),
        (MAX_LOGIN_ATTEMPTS > 0, "MAX_LOGIN_ATTEMPTS 必须为正"),
        (0 < MAX_DISCOUNT < 1, "MAX_DISCOUNT 必须在 (0, 1) 区间"),
        (BULK_THRESHOLD > 0, "BULK_THRESHOLD 必须为正"),
        (SMS_LENGTH_LIMIT > 0, "SMS_LENGTH_LIMIT 必须为正"),
    ]
    errors = [message for ok, message in checks if not ok]
    if errors:
        raise ValueError("阈值注册表校验失败: " + "; ".join(errors))
    return True

