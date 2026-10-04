"""配置热加载库（仅标准库）。

公开 API：
    ValidationError      整体校验失败，错误路径 -> 原因
    ConfigVersion        不可变配置快照
    ConfigStore          原子切换 / 回滚 / 重做
    JsonFileWatcher      文件变更监听与热加载
    validate_config      可独立使用的整体校验函数
"""

from .hotconfig import (
    ConfigStore,
    ConfigVersion,
    JsonFileWatcher,
    ValidationError,
    validate_config,
)

__all__ = [
    "ConfigStore",
    "ConfigVersion",
    "JsonFileWatcher",
    "ValidationError",
    "validate_config",
]
