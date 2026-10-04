class TransientError(Exception):
    """可重试的瞬时故障（网络抖动、下游超时等）。其他异常一律不重试。"""
