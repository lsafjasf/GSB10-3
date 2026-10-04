"""会话辅助（重构前）。"""

SESSION_TIMEOUT_MINUTES = 60  # 死代码：令牌校验迁到 auth.py 后全仓已无引用
MAX_LOGIN_ATTEMPTS = 5


def lockout_remaining(failed_attempts):
    return max(0, MAX_LOGIN_ATTEMPTS - failed_attempts)

