"""Order-handling module after the global-context refactor.

Every piece of per-request state lives on an explicit :class:`RequestContext`
instance that callers create and pass in. The module itself holds no mutable
state, so concurrent requests and parallel test workers cannot interfere.
"""

import warnings
from dataclasses import dataclass, field


class MissingContextError(RuntimeError):
    """Raised when a handler is called without a RequestContext."""


@dataclass
class RequestContext:
    request_id: str
    user_id: str
    discount: float = 0.0
    audit_log: list = field(default_factory=list)

    def audit(self, message):
        self.audit_log.append(
            {"request_id": self.request_id, "user_id": self.user_id, "message": message}
        )


def _require_context(ctx):
    if not isinstance(ctx, RequestContext):
        raise MissingContextError(
            "a RequestContext must be passed explicitly; no implicit global "
            "context exists anymore"
        )
    return ctx


def add_item(ctx, name, price):
    ctx = _require_context(ctx)
    ctx.audit("added " + name)
    return price


def handle_request(ctx, items):
    """Process one order bound to *ctx*. Returns the order result dict."""
    ctx = _require_context(ctx)
    ctx.audit("request started")
    subtotal = 0.0
    for name, price in items:
        subtotal += add_item(ctx, name, price)
    total = round(subtotal * (1 - ctx.discount), 2)
    ctx.audit("request finished")
    return {
        "request_id": ctx.request_id,
        "user_id": ctx.user_id,
        "total": total,
    }


def handle_request_legacy(request_id, user_id, items, discount=0.0):
    """Deprecated positional-argument shim for pre-refactor callers.

    It builds a fresh private context and delegates to :func:`handle_request`,
    so it never touches global state.
    """
    warnings.warn(
        "handle_request_legacy() is deprecated; build a RequestContext and "
        "call handle_request(ctx, items) instead",
        DeprecationWarning,
        stacklevel=2,
    )
    ctx = RequestContext(request_id=request_id, user_id=user_id, discount=discount)
    return handle_request(ctx, items)
