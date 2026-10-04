"""老测试固件工具（重构前）：阈值与生产代码重复定义。"""

SMS_LENGTH_LIMIT = 160      # 与 notifier.py 重复
MAX_DISCOUNT = 0.30         # 与 pricing.py 重复
FIXTURE_RATE_LIMIT = 7      # 仅测试使用：缩小限流构造夹具


def make_long_message():
    return "x" * (SMS_LENGTH_LIMIT + 1)


def discount_cap():
    return MAX_DISCOUNT

