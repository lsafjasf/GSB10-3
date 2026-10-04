"""失败隔离：把不可信调用（第三方模块）的任意异常收敛为 Err。

调用方只需要判断 Ok / Err，不再需要猜第三方会抛什么异常类型。
"""

from result import Ok, Err, Problem


def guard_call(stage, func, *args, **kwargs):
    """执行 func，任何异常都转换为 Err(Problem(stage=stage, ...))。"""
    try:
        return Ok(func(*args, **kwargs))
    except Exception as exc:  # 隔离边界：此处是唯一捕获点
        return Err([Problem(stage, "$", "%s: %s" % (type(exc).__name__, exc))])
