"""购物车（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import BULK_THRESHOLD, MAX_DISCOUNT


def cart_summary(quantities):
    total_qty = sum(quantities)
    return {
        "total_qty": total_qty,
        "bulk": total_qty >= BULK_THRESHOLD,
        "max_discount": MAX_DISCOUNT,
    }

