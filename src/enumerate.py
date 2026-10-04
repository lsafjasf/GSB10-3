"""enumerate —— 条件组合穷举（供构建脚本与回归测试共用）。

将连续输入（amount/qty）按业务分段离散化，与枚举维度
(vip, channel, region, coupon, birthday) 做笛卡尔积，得到
4 * 3 * 3 * 2 * 2 * 3 * 2 = 864 个条件组合。

每个组合附带一个"代表值"（amount/qty 取段内中点），
用于逐组合对拍；边界取值由 tests 与 build 脚本另行扫描。
"""

import itertools

# amount 分段（分）：[lo, hi]，hi 为 None 表示无上限
AMOUNT_BANDS = (
    (0, 4999),
    (5000, 9999),
    (10000, 19999),
    (20000, None),
)

# qty 分段
QTY_BANDS = (
    (1, 9),
    (10, 19),
    (20, None),
)

VIPS = ("normal", "silver", "gold")
CHANNELS = ("app", "web")
REGIONS = ("mainland", "remote")
COUPONS = ("none", "cash", "gift")
BIRTHDAYS = (False, True)


def band_index(value, bands):
    for idx, (lo, hi) in enumerate(bands):
        if value >= lo and (hi is None or value <= hi):
            return idx
    raise ValueError("value %r outside all bands" % (value,))


def band_mid(band):
    lo, hi = band
    if hi is None:
        return lo + 1  # 无上限段取 lo+1 作为代表值
    return (lo + hi) // 2


def all_combos():
    """产出全部条件组合。

    每个元素: (amount_band, qty_band, vip, channel, region, coupon, birthday)
    """
    return list(itertools.product(
        range(len(AMOUNT_BANDS)),
        range(len(QTY_BANDS)),
        VIPS,
        CHANNELS,
        REGIONS,
        COUPONS,
        BIRTHDAYS,
    ))


def representative(combo):
    """组合 -> 具体入参（取段内代表值）。"""
    amount_band, qty_band, vip, channel, region, coupon, birthday = combo
    return (
        band_mid(AMOUNT_BANDS[amount_band]),
        band_mid(QTY_BANDS[qty_band]),
        vip,
        channel,
        region,
        coupon,
        birthday,
    )


def combo_of(amount, qty, vip, channel, region, coupon, birthday):
    """具体入参 -> 所属组合（与 representative 互逆）。"""
    return (
        band_index(amount, AMOUNT_BANDS),
        band_index(qty, QTY_BANDS),
        vip,
        channel,
        region,
        coupon,
        birthday,
    )
