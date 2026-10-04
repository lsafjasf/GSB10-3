"""边界校验：在进入核心逻辑之前拒绝非法输入，并给出可读原因。

规则汇总见 docs/VALIDATION.md。所有规则只依赖标准库。
"""

import re

from result import Ok, Err, Problem

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")

_ORDER_FIELDS = {"order_id", "items", "coupon"}
_ITEM_FIELDS = {"sku", "qty", "unit_price"}

_BOOL_MSG = "bool 不是合法的数值类型"


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_unknown(fields, allowed, path, problems):
    for key in sorted(set(fields) - allowed):
        problems.append(Problem("validation", path, "未知字段 %r" % key))


def _validate_item(item, path, problems):
    if not isinstance(item, dict):
        problems.append(Problem("validation", path,
                                "必须是对象，实际为 %s" % type(item).__name__))
        return
    _check_unknown(item, _ITEM_FIELDS, path, problems)
    for field in ("sku", "qty", "unit_price"):
        if field not in item:
            problems.append(Problem("validation", "%s.%s" % (path, field), "缺少必填字段"))
    if "sku" in item:
        sku = item["sku"]
        if not isinstance(sku, str) or not sku.strip():
            problems.append(Problem("validation", "%s.sku" % path,
                                    "必须是非空字符串"))
    if "qty" in item:
        qty = item["qty"]
        if not _is_int(qty):
            msg = _BOOL_MSG if isinstance(qty, bool) else \
                "必须是整数，实际为 %s" % type(qty).__name__
            problems.append(Problem("validation", "%s.qty" % path, msg))
        elif qty <= 0:
            problems.append(Problem("validation", "%s.qty" % path,
                                    "必须是正整数，实际为 %d" % qty))
    if "unit_price" in item:
        price = item["unit_price"]
        if not _is_number(price):
            msg = _BOOL_MSG if isinstance(price, bool) else \
                "必须是数值，实际为 %s" % type(price).__name__
            problems.append(Problem("validation", "%s.unit_price" % path, msg))
        elif price < 0:
            problems.append(Problem("validation", "%s.unit_price" % path,
                                    "必须 >= 0，实际为 %r" % price))


def _validate_order(order, path, problems):
    if not isinstance(order, dict):
        problems.append(Problem("validation", path,
                                "必须是对象，实际为 %s" % type(order).__name__))
        return
    _check_unknown(order, _ORDER_FIELDS, path, problems)
    for field in ("order_id", "items"):
        if field not in order:
            problems.append(Problem("validation", "%s.%s" % (path, field), "缺少必填字段"))
    if "order_id" in order:
        oid = order["order_id"]
        if not isinstance(oid, str):
            problems.append(Problem("validation", "%s.order_id" % path,
                                    "必须是字符串，实际为 %s" % type(oid).__name__))
        elif not ORDER_ID_RE.match(oid):
            problems.append(Problem("validation", "%s.order_id" % path,
                                    "只允许字母/数字/连字符，长度 1-32"))
    if "coupon" in order and order["coupon"] is not None:
        coupon = order["coupon"]
        if not isinstance(coupon, str):
            problems.append(Problem("validation", "%s.coupon" % path,
                                    "必须是字符串或 null，实际为 %s" % type(coupon).__name__))
    if "items" in order:
        items = order["items"]
        if not isinstance(items, list):
            problems.append(Problem("validation", "%s.items" % path,
                                    "必须是数组，实际为 %s" % type(items).__name__))
        elif not items:
            problems.append(Problem("validation", "%s.items" % path, "至少包含一个条目"))
        else:
            for idx, item in enumerate(items):
                _validate_item(item, "%s.items[%d]" % (path, idx), problems)


def validate_orders(payload):
    """校验整个批处理输入。合法 -> Ok(payload)；非法 -> Err([Problem, ...])。

    只读校验，不修改 payload。
    """
    problems = []
    if not isinstance(payload, dict):
        problems.append(Problem("validation", "$",
                                "顶层必须是对象，实际为 %s" % type(payload).__name__))
        return Err(problems)
    _check_unknown(payload, {"orders"}, "$", problems)
    if "orders" not in payload:
        problems.append(Problem("validation", "$.orders", "缺少必填字段"))
        return Err(problems)
    orders = payload["orders"]
    if not isinstance(orders, list):
        problems.append(Problem("validation", "$.orders",
                                "必须是数组，实际为 %s" % type(orders).__name__))
        return Err(problems)
    for idx, order in enumerate(orders):
        _validate_order(order, "orders[%d]" % idx, problems)
    if problems:
        return Err(problems)
    return Ok(payload)
