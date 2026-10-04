"""会话辅助（重构后：阈值来自 app.thresholds）。

原 SESSION_TIMEOUT_MINUTES = 60 为死代码（全仓无引用），冲突处理见
docs/THRESHOLDS.md，收敛后不再保留。
"""

from app.thresholds import MAX_LOGIN_ATTEMPTS


def lockout_remaining(failed_attempts):
    return max(0, MAX_LOGIN_ATTEMPTS - failed_attempts)

