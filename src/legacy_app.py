"""旧版模块：用全局变量传递请求上下文。

存在并发污染问题，仅作为重构对比基线保留，不要再在新代码中使用。
"""

# ---- 全局可变状态（问题根源） ----
_current_request = None   # 当前请求对象
_current_user = None      # 当前登录用户
_request_id = None        # 当前请求 ID
g = {}                    # 请求级“杂货袋”，业务代码随手读写


def set_request(request):
    """把请求塞进全局变量（写）。"""
    global _current_request, _current_user, _request_id
    _current_request = request
    _current_user = request.get("user")
    _request_id = request.get("request_id")
    g.clear()
    g["request_id"] = _request_id


def get_request():
    """读全局请求。"""
    return _current_request


def current_user():
    """读全局用户。"""
    return _current_user


def get_request_id():
    """读全局请求 ID。"""
    return _request_id


def handle_request(request, barrier=None):
    """业务入口：先写全局，再在处理过程中读全局。"""
    set_request(request)
    if barrier is not None:
        barrier.wait()  # 强制各线程在此交错，放大竞争窗口（测试用）
    # 模拟业务处理：多处隐式读全局
    user = current_user()
    rid = get_request_id()
    g["handler"] = "handle_request"
    return {
        "request_id": rid,
        "user": user,
        "g_request_id": g.get("request_id"),
    }
