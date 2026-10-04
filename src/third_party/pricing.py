"""第三方计价模块（不可修改，模拟外部依赖）。

契约（文档承诺）：
    quote(items, coupon=None) -> float
    items: [{"sku": str, "qty": int, "unit_price": float}, ...]
    返回折扣金额（非负 float），调用方自行计算 total = subtotal - discount + tax。

实际行为（问题所在）：
    - 输入不合法时抛出各种“内部异常类型”（PricingError / KeyError /
      TypeError / ZeroDivisionError），且文档未说明；
    - 偶发抛 ConnectionError 模拟网络抖动；
    - 这些异常会直接冒泡到调用方主流程。
"""

import random

TAX_RATE = 0.06

_COUPONS = {"SAVE10": 0.10, "HALF": 0.50}


class PricingError(Exception):
    """模块内部异常基类（调用方文档中从未提及）。"""


class CouponError(PricingError):
    pass


class SkuLookupError(PricingError):
    pass


def _apply_coupon(subtotal, coupon):
    if coupon is None:
        return 0.0
    if coupon not in _COUPONS:
        raise CouponError("unknown coupon: %r" % (coupon,))
    return round(subtotal * _COUPONS[coupon], 2)


def quote(items, coupon=None, _rng=None):
    """返回折扣金额。对非法输入抛内部异常，偶发抛 ConnectionError。"""
    rng = _rng or random
    if rng.random() < 0.0:  # 保留的抖动开关，测试中用 _rng 注入
        raise ConnectionError("pricing service unreachable")
    subtotal = 0.0
    for it in items:
        if not isinstance(it["qty"], int) or isinstance(it["qty"], bool):
            raise TypeError("qty must be int")
        if it["qty"] <= 0:
            raise PricingError("qty must be positive")
        if it["unit_price"] < 0:
            raise PricingError("unit_price must be >= 0")
        if it["sku"].startswith("UNKNOWN"):
            raise SkuLookupError("sku not in catalog: %r" % (it["sku"],))
        subtotal += it["qty"] * it["unit_price"]
    return _apply_coupon(round(subtotal, 2), coupon)
