"""明确的结果类型：调用方只面对 Ok / Err，不再需要猜异常类型。"""

from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass(frozen=True)
class Problem:
    """一条可读的问题描述。

    stage:  "validation"（边界校验拒绝） | "pricing"（第三方模块失败） | "io"
    path:   出错位置，如 "orders[2].items[0].qty"；顶层为 "$"
    reason: 人类可读原因
    """

    stage: str
    path: str
    reason: str

    def __str__(self):
        return "[%s] %s: %s" % (self.stage, self.path, self.reason)


@dataclass(frozen=True)
class Ok:
    value: Any

    @property
    def is_ok(self):
        return True

    def unwrap(self):
        return self.value


@dataclass(frozen=True)
class Err:
    problems: List[Problem]

    @property
    def is_ok(self):
        return False

    def unwrap(self):
        raise RuntimeError("called unwrap() on Err: %s" % (self.problems,))


Result = Any  # Ok | Err；用 isinstance(x, Ok) 判定


@dataclass(frozen=True)
class BatchOutcome:
    """批处理结果。records 只包含成功订单；失败订单全部进 failures。"""

    records: list
    failures: List[Problem]
    output_path: Optional[str]
