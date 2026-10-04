"""定价（重构后：阈值来自 app.thresholds）。"""

from app.thresholds import BULK_THRESHOLD, MAX_DISCOUNT


def price(quantity, unit_price, discount):
    applied = min(discount, MAX_DISCOUNT)
    total = quantity * unit_price * (1 - applied)
    if quantity >= BULK_THRESHOLD:
        total *= 0.95
    return round(total, 2)

