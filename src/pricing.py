"""表驱动重构版运费报价。

与 legacy_pricing.quote 逐组合行为等价（见 tools/diff_test.py），
但所有"优先级"都体现在 GUARDS 表的顺序里，不再藏在嵌套深度中。

规则分两层：
1. GUARDS —— 有序守卫规则，按顺序命中即抛异常或提前返回；
2. 费用表 —— 守卫全部放行后，由 BASE_FEE / EXPRESS_SURCHARGE / DISCOUNT 等表算运费。
"""

from collections import namedtuple

USER_LEVELS = ("normal", "silver", "gold", "staff")
REGIONS = ("domestic", "remote", "overseas")
CATEGORIES = ("normal", "fragile", "frozen")
ACCOUNT_STATUS = ("active", "frozen", "banned")

FREE_SHIPPING_THRESHOLD = 500.0
HOLIDAY_EXPRESS_SURCHARGE = 5.0

# 基础运费（区域, 品类）。(remote/overseas, frozen) 不在表中，由守卫拦截。
BASE_FEE = {
    ("domestic", "normal"): 6.0,
    ("domestic", "fragile"): 14.0,
    ("domestic", "frozen"): 18.0,
    ("remote", "normal"): 15.0,
    ("remote", "fragile"): 23.0,
    ("overseas", "normal"): 40.0,
    ("overseas", "fragile"): 48.0,
}

EXPRESS_SURCHARGE = {"domestic": 10.0, "remote": 25.0}  # overseas 由守卫拦截

# 折扣率。silver 仅 domestic 生效（保持原实现遗漏现状，见 docs/UNREACHABLE.md G2）。
DISCOUNT = {"silver": 0.05, "gold": 0.10, "staff": 0.30}
STAFF_HOLIDAY_FLAT_OFF = 5.0

Guard = namedtuple("Guard", ["rule_id", "description", "matches", "outcome"])


def _raise(exc_type, message):
    """生成一个命中即抛异常的 outcome；message 为 str 时按 ctx 格式化。"""
    def outcome(ctx):
        text = message % ctx if isinstance(message, str) and "%(" in message else message
        raise exc_type(text)
    return outcome


def _return(value):
    return lambda ctx: value


# 守卫顺序 = 原实现中分支被命中的真实优先级，逐条对应 legacy_pricing.py 的判定路径。
GUARDS = [
    Guard("R01", "金额为 0 的空单（注意：早于封号检查，保持原实现行为，见 UNREACHABLE.md G1）",
          lambda c: c["amount"] == 0, _return(0.0)),
    Guard("R02", "金额为负",
          lambda c: c["amount"] < 0,
          _raise(ValueError, "amount must be non-negative")),
    Guard("R03", "账号被封禁",
          lambda c: c["account_status"] == "banned",
          _raise(PermissionError, "account is banned")),
    Guard("R04", "账号状态未知",
          lambda c: c["account_status"] not in ACCOUNT_STATUS,
          _raise(ValueError, "unknown account_status: %(account_status)r")),

    # —— frozen 账号（原实现 frozen 分支的检查顺序：海外 → 加急 → 区域 → 冷冻品 → 品类）——
    Guard("R10", "冻结账号禁止寄海外",
          lambda c: c["account_status"] == "frozen" and c["region"] == "overseas",
          _raise(PermissionError, "frozen account cannot ship overseas")),
    Guard("R11", "冻结账号禁止加急",
          lambda c: c["account_status"] == "frozen" and c["express"],
          _raise(RuntimeError, "frozen account cannot use express")),
    Guard("R12", "区域未知（frozen 账号在区域分派处报错）",
          lambda c: c["account_status"] == "frozen" and c["region"] not in REGIONS,
          _raise(ValueError, "unknown region: %(region)r")),
    Guard("R13", "冷冻品禁寄偏远地区（frozen 账号）",
          lambda c: c["account_status"] == "frozen"
                    and c["region"] == "remote" and c["category"] == "frozen",
          _raise(ValueError, "frozen goods cannot be shipped to remote area")),
    Guard("R14", "品类未知（frozen 账号）",
          lambda c: c["account_status"] == "frozen" and c["category"] not in CATEGORIES,
          _raise(ValueError, "unknown category: %(category)r")),

    # —— active 账号（原实现 active 分支的检查顺序：区域 → 冷冻品 → 海外加急 → 品类）——
    Guard("R20", "区域未知",
          lambda c: c["region"] not in REGIONS,
          _raise(ValueError, "unknown region: %(region)r")),
    Guard("R21", "冷冻品禁寄偏远地区",
          lambda c: c["region"] == "remote" and c["category"] == "frozen",
          _raise(ValueError, "frozen goods cannot be shipped to remote area")),
    Guard("R22", "冷冻品禁寄海外",
          lambda c: c["region"] == "overseas" and c["category"] == "frozen",
          _raise(ValueError, "frozen goods cannot be shipped overseas")),
    Guard("R23", "海外订单不支持加急",
          lambda c: c["region"] == "overseas" and c["express"],
          _raise(RuntimeError, "express not available for overseas orders")),
    Guard("R24", "品类未知",
          lambda c: c["category"] not in CATEGORIES,
          _raise(ValueError, "unknown category: %(category)r")),
]


def _apply_discount(fee, ctx):
    level = ctx["user_level"]
    if level == "silver":
        if ctx["region"] == "domestic":
            fee *= 1 - DISCOUNT["silver"]
    elif level == "gold":
        fee *= 1 - DISCOUNT["gold"]
    elif level == "staff":
        fee *= 1 - DISCOUNT["staff"]
        if ctx["holiday"]:
            fee -= STAFF_HOLIDAY_FLAT_OFF
    # 未知 level：无折扣（保持原实现行为，见 docs/UNREACHABLE.md G3）
    return fee


def _fee(ctx):
    if ctx["account_status"] == "active":
        fee = BASE_FEE[(ctx["region"], ctx["category"])]
        if ctx["region"] != "overseas" and ctx["amount"] >= FREE_SHIPPING_THRESHOLD:
            fee = 0.0
        if ctx["express"]:
            fee += EXPRESS_SURCHARGE[ctx["region"]]
            if ctx["holiday"]:
                fee += HOLIDAY_EXPRESS_SURCHARGE
        fee = _apply_discount(fee, ctx)
        return round(max(fee, 0.0), 2)
    # frozen 账号：只有基础运费，无免邮、无折扣、无加急（加急已被 R11 拦截）
    return round(BASE_FEE[(ctx["region"], ctx["category"])], 2)


def quote(order):
    ctx = {
        "amount": order["amount"],
        "user_level": order["user_level"],
        "region": order["region"],
        "category": order["category"],
        "express": bool(order["express"]),
        "holiday": bool(order["holiday"]),
        "account_status": order["account_status"],
    }
    for guard in GUARDS:
        if guard.matches(ctx):
            return guard.outcome(ctx)
    return _fee(ctx)
