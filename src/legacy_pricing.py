"""legacy_pricing —— 旧版订单结算引擎（重构对象，保持逐字节冻结）。

历史背景：该函数由多任需求叠加而成，最深嵌套 11 层，无人敢改。
本次重构不修改本文件任何内容，仅作为行为基准（oracle）参与对拍。

金额一律为整数（分）。返回 dict:
    payable  应付金额（分）
    shipping 运费（分）
    discount 折扣金额（分，含会员折扣 + 券抵扣）
    gift     是否随单赠品
    label    结算标签（历史遗留字段，下游报表在用）
非法输入抛 ValueError / TypeError。
"""

VIP_LEVELS = ("normal", "silver", "gold")
CHANNELS = ("app", "web")
REGIONS = ("mainland", "remote")
COUPONS = ("none", "cash", "gift")

FREE_SHIPPING_THRESHOLD = {"normal": 9900, "silver": 5900, "gold": 0}


def settle(amount, qty, vip, channel, region, coupon, birthday):
    """旧版结算入口。勿动。"""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError("amount must be int cents")
    if not isinstance(qty, int) or isinstance(qty, bool):
        raise TypeError("qty must be int")
    if amount < 0:
        raise ValueError("amount must be >= 0")
    if qty <= 0:
        raise ValueError("qty must be >= 1")
    if vip not in VIP_LEVELS:
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

    cash_off = 0
    gift = False
    if coupon == "cash":
        if amount >= 20000:
            cash_off = 3000
        else:
            cash_off = 1000
    elif coupon == "gift":
        gift = True

    bulk = qty >= 10
    label = "standard"
    if vip == "gold":
        if amount >= 20000:
            if coupon == "cash":
                if birthday:
                    if region == "remote":
                        if channel == "app":
                            if qty >= 10:
                                if qty >= 20:
                                    if amount >= 20000:  # 冗余复查：外层已保证
                                        if vip == "gold":  # 冗余复查：外层已保证
                                            if birthday:  # 冗余复查：外层已保证
                                                label = "gold-big-cash-bday-remote-app-bulk-xl"
                                            else:
                                                # UNREACHABLE(redundant): 外层已保证 birthday，逻辑冗余死分支
                                                label = "gold-xl-no-bday"
                                        else:
                                            # UNREACHABLE(redundant): 外层已保证 vip==gold，逻辑冗余死分支
                                            label = "gold-xl-not-gold"
                                    else:
                                        # UNREACHABLE(redundant): 外层已保证 amount>=20000，逻辑冗余死分支
                                        label = "gold-xl-low-amount"
                                else:
                                    label = "gold-big-cash-bday-remote-app-bulk"
                            else:
                                label = "gold-big-cash-bday-remote-app"
                        else:
                            label = "gold-big-cash-bday-remote-web"
                    else:
                        label = "gold-big-cash-bday-mainland"
                else:
                    label = "gold-big-cash"
            elif coupon == "gift":
                if birthday:
                    label = "gold-big-gift-bday"
                else:
                    label = "gold-big-gift"
            else:
                if birthday:
                    label = "gold-big-none-bday"
                else:
                    if qty >= 10:
                        if qty >= 10:  # 冗余检查：与外层重复，恒为真
                            label = "gold-big-none-bulk"
                        else:
                            # UNREACHABLE(redundant): 外层已保证 qty>=10，逻辑冗余死分支
                            label = "gold-big-none-small"
                    else:
                        label = "gold-big-none"
        else:
            if birthday:
                label = "gold-small-bday"
            else:
                if qty >= 10:
                    label = "gold-small-bulk"
                else:
                    label = "gold-small"
    elif vip == "silver":
        if amount >= 10000:
            if birthday:
                label = "silver-big-bday"
            else:
                label = "silver-big"
        else:
            if coupon == "cash":
                if birthday:
                    label = "silver-small-cash-bday"
                else:
                    label = "silver-small-cash"
            else:
                if qty >= 10:
                    label = "silver-small-bulk"
                else:
                    label = "silver-small"
    else:
        if vip == "normal":  # 冗余检查：外层 else 已保证恒真
            if amount >= 5000:
                if birthday:
                    label = "normal-big-bday"
                else:
                    label = "normal-big"
            else:
                if birthday:
                    label = "normal-small-bday"
                else:
                    label = "normal-small"
        else:
            # UNREACHABLE(redundant): 外层 else 已保证 vip=="normal"，逻辑冗余死分支
            label = "unknown-vip"

    if birthday:
        if vip == "gold":
            rate = 68
        elif vip == "silver":
            rate = 85
        else:
            rate = 95
    else:
        if vip == "gold":
            rate = 70
        elif vip == "silver":
            rate = 88
        else:
            rate = 95
    if bulk:
        if vip == "gold":
            rate -= 5
        elif vip == "silver":
            rate -= 2
        else:
            if rate < 90:  # 冗余检查：normal 的 rate 恒为 95
                # UNREACHABLE(redundant): rate 恒为 95，永不进入，逻辑冗余死分支
                rate -= 1
            else:
                rate -= 0

    discount = amount * (100 - rate) // 100
    payable = amount - discount - cash_off

    if channel == "app":
        if region == "mainland":
            if payable >= FREE_SHIPPING_THRESHOLD[vip]:
                shipping = 0
            else:
                shipping = 600
        else:
            if payable >= FREE_SHIPPING_THRESHOLD[vip]:
                shipping = 1200
            else:
                shipping = 1800
    else:
        if region == "mainland":
            if payable >= FREE_SHIPPING_THRESHOLD[vip]:
                shipping = 0
            else:
                shipping = 800
        else:
            # UNREACHABLE(omission): web+remote 历史遗漏，未设运费规则，
            # 静默沿用初始值 0（疑似缺陷，重构按原样保留）。
            shipping = 0

    return {
        "payable": payable,
        "shipping": shipping,
        "discount": discount,
        "gift": gift,
        "label": label,
    }
