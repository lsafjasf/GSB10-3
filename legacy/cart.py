"""购物车（重构前）。"""

MAX_DISCOUNT = 0.30
BULK_THRESHOLD = 20


def cart_summary(quantities):
    total_qty = sum(quantities)
    return {
        "total_qty": total_qty,
        "bulk": total_qty >= BULK_THRESHOLD,
        "max_discount": MAX_DISCOUNT,
    }

