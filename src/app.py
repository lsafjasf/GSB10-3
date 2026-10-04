"""重构后模块：请求上下文显式传入，无任何隐式全局读写。

用法：
    ctx = RequestContext(request)
    result = handle_request(ctx)
"""


class ContextMissingError(RuntimeError):
    """调用方未显式提供 RequestContext 时抛出。"""


class RequestContext:
    """每个请求独占一份，随调用链显式传递，线程间天然隔离。"""

    __slots__ = ("request", "user", "request_id", "g")

    def __init__(self, request):
        if request is None:
            raise ContextMissingError(
                "request 不能为 None：请为每个请求创建独立的 RequestContext"
            )
        self.request = request
        self.user = request.get("user")
        self.request_id = request.get("request_id")
        self.g = {"request_id": self.request_id}


def _require_ctx(ctx):
    if ctx is None or not isinstance(ctx, RequestContext):
        raise ContextMissingError(
            "缺少请求上下文：必须显式传入 RequestContext 实例，禁止依赖全局变量"
        )
    return ctx


def handle_request(ctx, barrier=None):
    """业务入口：所有请求相关状态都从显式传入的 ctx 读取。"""
    _require_ctx(ctx)
    if barrier is not None:
        barrier.wait()  # 与旧版相同的交错点，用于并发对比
    ctx.g["handler"] = "handle_request"
    return {
        "request_id": ctx.request_id,
        "user": ctx.user,
        "g_request_id": ctx.g.get("request_id"),
    }


def _removed_global_api(name):
    """旧的全局访问器：调用即失败，防止隐式全局读写悄悄复活。"""

    def _raiser(*args, **kwargs):
        raise RuntimeError(
            "%s() 已移除：全局请求上下文已废弃，"
            "请创建 RequestContext 并显式传入" % name
        )

    return _raiser


set_request = _removed_global_api("set_request")
get_request = _removed_global_api("get_request")
current_user = _removed_global_api("current_user")
get_request_id = _removed_global_api("get_request_id")
