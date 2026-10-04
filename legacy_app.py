"""Legacy order-handling module (DEPRECATED).

This module is kept for reference and for compatibility tests. It passes
per-request data through module-level global variables. Concurrent requests
overwrite each other's context, and state leaks between tests.

Use :mod:`app` instead, which carries an explicit ``RequestContext``.
"""

# ---------------------------------------------------------------------------
# Global mutable state (the thing being refactored away)
# ---------------------------------------------------------------------------
_current_request = None
_audit_log = []
_discount_rate = 0.0

# Test hook: called between "context installed" and "context consumed".
# Used by scripts/concurrency_compare.py to force deterministic interleaving.
_inter_request_hook = None


def set_request(request_id, user_id):
    global _current_request
    _current_request = {"request_id": request_id, "user_id": user_id}


def get_current_request():
    return _current_request


def set_discount(rate):
    global _discount_rate
    _discount_rate = rate


def audit(message):
    req = _current_request
    _audit_log.append(
        {
            "request_id": req["request_id"] if req is not None else None,
            "user_id": req["user_id"] if req is not None else None,
            "message": message,
        }
    )


def get_audit_log():
    return list(_audit_log)


def _run_hook():
    if _inter_request_hook is not None:
        _inter_request_hook()


def handle_request(request_id, user_id, items, discount=0.0):
    """Process one order using module globals to hold the request context."""
    set_request(request_id, user_id)
    set_discount(discount)
    audit("request started")
    subtotal = 0.0
    for name, price in items:
        subtotal += price
        audit("added " + name)
    _run_hook()
    total = round(subtotal * (1 - _discount_rate), 2)
    current = _current_request
    audit("request finished")
    return {
        "request_id": current["request_id"],
        "user_id": current["user_id"],
        "total": total,
    }
