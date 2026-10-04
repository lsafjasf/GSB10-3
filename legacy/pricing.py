"""定价（重构前）。"""

MAX_DISCOUNT = 0.30
BULK_THRESHOLD = 20


def price(quantity, unit_price, discount):
    applied = min(discount, MAX_DISCOUNT)
    total = quantity * unit_price * (1 - applied)
    if quantity >= BULK_THRESHOLD:
        total *= 0.95
    return round(total, 2)

