"""pricing —— 表驱动重构版订单结算引擎。

与 src/legacy_pricing.py 行为完全一致（由 tests/ 与 artifacts/ 对拍证明），
但业务分支全部替换为查表：本模块的 settle() 中没有任何 if 语句
（结构约束由 tests/test_regression.py::TestStructure 强制）。

表结构：
    RATE_TABLE       (vip, birthday, bulk) -> 折扣率（%）
    CASH_OFF_TABLE   (amount_band)       -> 现金券抵扣（分）
    GIFT_COUPON      赠品券常量
    SHIPPING_TABLE   (channel, region)   -> (未达包邮线运费, 达包邮线运费)
    FREE_SHIPPING    vip -> 包邮门槛（分）
    LABEL_RULES      有序通配规则表 -> 结算标签
"""

from enumerate import AMOUNT_BANDS, QTY_BANDS, VIPS, band_index
from legacy_pricing import CHANNELS, REGIONS, COUPONS

# --- 折扣率表：key = (vip, birthday, bulk) -------------------------------
_RATE_BASE = {
    ("gold", True): 68, ("gold", False): 70,
    ("silver", True): 85, ("silver", False): 88,
    ("normal", True): 95, ("normal", False): 95,
}
_BULK_OFF = {"gold": 5, "silver": 2, "normal": 0}
RATE_TABLE = {
    (vip, birthday, bulk): _RATE_BASE[(vip, birthday)] - (_BULK_OFF[vip] if bulk else 0)
    for vip in VIPS for birthday in (False, True) for bulk in (False, True)
}

# --- 券表 -----------------------------------------------------------------
CASH_OFF_TABLE = {("cash", band): off for band, off in
                  ((0, 1000), (1, 1000), (2, 1000), (3, 3000))}
GIFT_TABLE = {"gift": True}

# --- 运费表：key = (channel, region)，值 = (未达包邮线, 达包邮线) ----------
# 注：("web", "remote") 为原实现的遗漏路径（静默 0），按原样保留。
SHIPPING_TABLE = {
    ("app", "mainland"): (600, 0),
    ("app", "remote"): (1800, 1200),
    ("web", "mainland"): (800, 0),
    ("web", "remote"): (0, 0),
}
FREE_SHIPPING = {"normal": 9900, "silver": 5900, "gold": 0}

# --- 结算标签：有序通配规则表（先匹配先生效） ----------------------------
# 模式元素：None 为通配；tuple 为集合匹配；其余为等值匹配。
# 模式字段顺序: (vip, amount_band, coupon, birthday, region, channel, qty_band)
LABEL_RULES = (
    (("gold", 3, "cash", True, "remote", "app", 2), "gold-big-cash-bday-remote-app-bulk-xl"),
    (("gold", 3, "cash", True, "remote", "app", 1), "gold-big-cash-bday-remote-app-bulk"),
    (("gold", 3, "cash", True, "remote", "app", 0), "gold-big-cash-bday-remote-app"),
    (("gold", 3, "cash", True, "remote", "web", None), "gold-big-cash-bday-remote-web"),
    (("gold", 3, "cash", True, "mainland", None, None), "gold-big-cash-bday-mainland"),
    (("gold", 3, "cash", False, None, None, None), "gold-big-cash"),
    (("gold", 3, "gift", True, None, None, None), "gold-big-gift-bday"),
    (("gold", 3, "gift", False, None, None, None), "gold-big-gift"),
    (("gold", 3, "none", True, None, None, None), "gold-big-none-bday"),
    (("gold", 3, "none", False, None, None, (1, 2)), "gold-big-none-bulk"),
    (("gold", 3, "none", False, None, None, 0), "gold-big-none"),
    (("gold", (0, 1, 2), None, True, None, None, None), "gold-small-bday"),
    (("gold", (0, 1, 2), None, False, None, None, (1, 2)), "gold-small-bulk"),
    (("gold", (0, 1, 2), None, False, None, None, 0), "gold-small"),
    (("silver", (2, 3), None, True, None, None, None), "silver-big-bday"),
    (("silver", (2, 3), None, False, None, None, None), "silver-big"),
    (("silver", (0, 1), "cash", True, None, None, None), "silver-small-cash-bday"),
    (("silver", (0, 1), "cash", False, None, None, None), "silver-small-cash"),
    (("silver", (0, 1), ("none", "gift"), None, None, None, (1, 2)), "silver-small-bulk"),
    (("silver", (0, 1), ("none", "gift"), None, None, None, 0), "silver-small"),
    (("normal", (1, 2, 3), None, True, None, None, None), "normal-big-bday"),
    (("normal", (1, 2, 3), None, False, None, None, None), "normal-big"),
    (("normal", 0, None, True, None, None, None), "normal-small-bday"),
    (("normal", 0, None, False, None, None, None), "normal-small"),
)
DEFAULT_LABEL = "standard"


def _match(pattern, key):
    return all(
        p is None or (k in p if isinstance(p, tuple) else k == p)
        for p, k in zip(pattern, key)
    )


def _label(vip, amount_band, coupon, birthday, region, channel, bulk):
    key = (vip, amount_band, coupon, birthday, region, channel, bulk)
    for pattern, label in LABEL_RULES:
        if _match(pattern, key):
            return label
    return DEFAULT_LABEL


def _validate(amount, qty, vip, channel, region, coupon):
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError("amount must be int cents")
    if not isinstance(qty, int) or isinstance(qty, bool):
        raise TypeError("qty must be int")
    if amount < 0:
        raise ValueError("amount must be >= 0")
    if qty <= 0:
        raise ValueError("qty must be >= 1")
    if vip not in VIPS:
        raise ValueError("unknown vip level")
    if channel not in CHANNELS:
        raise ValueError("unknown channel")
    if region not in REGIONS:
        raise ValueError("unknown region")
    if coupon not in COUPONS:
        raise ValueError("unknown coupon")
    if coupon == "cash" and amount < 5000:
        raise ValueError("cash coupon requires amount >= 5000")
    if coupon == "gift" and amount < 20000:
        raise ValueError("gift coupon requires amount >= 20000")


def settle(amount, qty, vip, channel, region, coupon, birthday):
    """表驱动结算入口。签名与 legacy_pricing.settle 完全一致。"""
    _validate(amount, qty, vip, channel, region, coupon)

    amount_band = band_index(amount, AMOUNT_BANDS)
    qty_band = band_index(qty, QTY_BANDS)
    bulk = qty_band >= 1

    rate = RATE_TABLE[(vip, birthday, bulk)]
    discount = amount * (100 - rate) // 100

    cash_off = CASH_OFF_TABLE.get((coupon, amount_band), 0)
    gift = GIFT_TABLE.get(coupon, False)

    payable = amount - discount - cash_off

    shipping = SHIPPING_TABLE[(channel, region)][payable >= FREE_SHIPPING[vip]]

    return {
        "payable": payable,
        "shipping": shipping,
        "discount": discount,
        "gift": gift,
        "label": _label(vip, amount_band, coupon, birthday, region, channel, qty_band),
    }
