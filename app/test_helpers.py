"""测试固件工具（重构后）：与生产重复的阈值改为引用注册表。"""

from app.thresholds import MAX_DISCOUNT, SMS_LENGTH_LIMIT

# 仅测试使用：缩小限流构造夹具。不属于业务阈值，有意不进注册表。
FIXTURE_RATE_LIMIT = 7


def make_long_message():
    return "x" * (SMS_LENGTH_LIMIT + 1)


def discount_cap():
    return MAX_DISCOUNT

