"""历史遗留的运费报价实现（重构基准，冻结勿改）。

订单字段：
    amount         订单金额（float）
    user_level     "normal" | "silver" | "gold" | "staff"
    region         "domestic" | "remote" | "overseas"
    category       "normal" | "fragile" | "frozen"
    express        是否加急
    holiday        是否节假日下单
    account_status "active" | "frozen" | "banned"

返回值：运费（float，保留两位小数）；无法报价时抛异常。
分支优先级没有任何文档，只能靠读代码猜。重构期间本文件仅作回归对拍基准。
"""


def quote(order):
    amount = order["amount"]
    if amount == 0:                                     # L1 空单直接免费（在封号检查之前！）
        return 0.0
    if amount < 0:                                      # L1
        raise ValueError("amount must be non-negative")

    status = order["account_status"]
    if status == "banned":                              # L1
        raise PermissionError("account is banned")

    level = order["user_level"]
    region = order["region"]
    category = order["category"]
    express = bool(order["express"])
    holiday = bool(order["holiday"])

    if status == "active":                              # L1
        if region == "domestic":                        # L2
            if category == "normal":                    # L3
                if express:                             # L4
                    if amount >= 500:                   # L5
                        fee = 10.0
                    else:
                        fee = 16.0
                    if holiday:                         # L5
                        fee = fee + 5.0
                else:
                    if amount >= 500:                   # L5
                        fee = 0.0
                    else:
                        fee = 6.0
            elif category == "fragile":                 # L3
                if express:                             # L4
                    if amount >= 500:                   # L5
                        fee = 10.0
                    else:
                        fee = 24.0
                    if holiday:                         # L5
                        fee = fee + 5.0
                else:
                    if amount >= 500:                   # L5
                        fee = 0.0
                    else:
                        fee = 14.0
            elif category == "frozen":                  # L3
                if express:                             # L4
                    if amount >= 500:                   # L5
                        fee = 10.0
                    else:
                        fee = 28.0
                    if holiday:                         # L5
                        fee = fee + 5.0
                else:
                    if amount >= 500:                   # L5
                        fee = 0.0
                    else:
                        fee = 18.0
            else:
                raise ValueError("unknown category: %r" % (category,))
        elif region == "remote":                        # L2
            if category == "frozen":                    # L3
                raise ValueError("frozen goods cannot be shipped to remote area")
            elif category == "normal":                  # L3
                if express:                             # L4
                    if amount >= 500:                   # L5
                        fee = 25.0
                    else:
                        fee = 40.0
                    if holiday:                         # L5
                        fee = fee + 5.0
                else:
                    if amount >= 500:                   # L5
                        fee = 0.0
                    else:
                        fee = 15.0
            elif category == "fragile":                 # L3
                if express:                             # L4
                    if amount >= 500:                   # L5
                        fee = 25.0
                    else:
                        fee = 48.0
                    if holiday:                         # L5
                        fee = fee + 5.0
                else:
                    if amount >= 500:                   # L5
                        fee = 0.0
                    else:
                        fee = 23.0
            else:
                raise ValueError("unknown category: %r" % (category,))
        elif region == "overseas":                      # L2
            if category == "frozen":                    # L3
                raise ValueError("frozen goods cannot be shipped overseas")
            if express:                                 # L3
                raise RuntimeError("express not available for overseas orders")
            if category == "normal":                    # L3
                fee = 40.0                              # L4 海外无免邮
            elif category == "fragile":
                fee = 48.0
            else:
                raise ValueError("unknown category: %r" % (category,))
        else:
            raise ValueError("unknown region: %r" % (region,))

        # —— 折扣区（只有 active 账号能走到这里）——
        if level == "silver":                           # L2
            if region == "domestic":                    # L3
                fee = fee * 0.95
            # 其它地区 silver 无折扣 —— 疑似需求遗漏
        elif level == "gold":                           # L2
            if region == "domestic":                    # L3
                fee = fee * 0.90
            elif region == "remote":
                fee = fee * 0.90
            else:
                fee = fee * 0.90                        # 三个分支完全一样：逻辑冗余
        elif level == "staff":                          # L2
            fee = fee * 0.70
            if holiday:                                 # L3
                if region == "overseas":                # L4
                    if express:                         # L5 死代码：overseas+express 前面已 raise
                        fee = fee * 0.5
                    else:
                        fee = fee - 5.0
                else:
                    fee = fee - 5.0
        # 未知 level 落到这里：无折扣（未校验，疑似遗漏）
        if fee < 0:                                     # L2
            fee = 0.0
    elif status == "frozen":                            # L1
        if region == "overseas":                        # L2
            raise PermissionError("frozen account cannot ship overseas")
        if express:                                     # L2
            raise RuntimeError("frozen account cannot use express")
        if region == "domestic":                        # L2
            if category == "normal":                    # L3
                fee = 6.0
            elif category == "fragile":
                fee = 14.0
            elif category == "frozen":
                fee = 18.0
            else:
                raise ValueError("unknown category: %r" % (category,))
        elif region == "remote":                        # L2
            if category == "frozen":                    # L3
                raise ValueError("frozen goods cannot be shipped to remote area")
            elif category == "normal":
                fee = 15.0
            elif category == "fragile":
                fee = 23.0
            else:
                raise ValueError("unknown category: %r" % (category,))
        else:
            raise ValueError("unknown region: %r" % (region,))
        if holiday:                                     # L2
            if express:                                 # L3 死代码：express 前面已 raise
                fee = fee + 5.0
        # 冻结账号：无折扣、无免邮
    else:
        raise ValueError("unknown account_status: %r" % (status,))

    return round(fee, 2)
