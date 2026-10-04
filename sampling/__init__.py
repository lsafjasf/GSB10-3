"""分层日志采样库（仅依赖 Python 3 标准库）。

公开 API：
    Level, Event, Rule, Match
    LayeredSampler      分级/分通道采样器（规则可原子更新、带匹配优先级）
    BufferedRequestSampler   请求级一致性（尾部决策）：同一请求要么全留要么全丢
    HeadRequestSampler       请求级一致性（流式 head 决策 + 错误升级）
    Metrics                   按级别/通道聚合，计算实际采样率与目标采样率偏差
    InconsistentRequestError  请求级一致性断言失败异常
    assert_request_consistency
"""

from .layered_sampler import (
    Level,
    Event,
    Rule,
    Match,
    LayeredSampler,
    InconsistentRequestError,
    assert_request_consistency,
)
from .request_sampler import (
    BufferedRequestSampler,
    HeadRequestSampler,
)
from .metrics import Metrics, GroupStats

__all__ = [
    "Level",
    "Event",
    "Rule",
    "Match",
    "LayeredSampler",
    "BufferedRequestSampler",
    "HeadRequestSampler",
    "Metrics",
    "GroupStats",
    "InconsistentRequestError",
    "assert_request_consistency",
]
