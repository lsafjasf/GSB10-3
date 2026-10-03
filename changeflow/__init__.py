"""changeflow: 数据库变更流的分发与顺序保证（仅标准库）。

- 同一主键的变更严格按产生顺序（全局位点 seq）投递；
- 不同主键由线程池并行处理；
- 消费者确认按位点持久化（WAL + 连续水位线），未确认的变更重启后重投；
- 提供全局重排工具，把并行消费结果还原为全局顺序。
"""

from .model import Change
from .log import ChangeLog
from .offsets import OffsetStore
from .dispatcher import Dispatcher, assert_per_key_order, global_reorder

__all__ = [
    "Change",
    "ChangeLog",
    "OffsetStore",
    "Dispatcher",
    "assert_per_key_order",
    "global_reorder",
]
