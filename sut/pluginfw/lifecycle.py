"""插件生命周期状态定义与转移校验。

注意：只有经过 transition() 的状态变化才会被校验；
框架中并非所有状态写入都经过这里（见 loader.py / manager.py）。
"""

DISCOVERED = "discovered"
LOADED = "loaded"
INITIALIZED = "initialized"
FAILED = "failed"
UNLOADING = "unloading"
UNLOADED = "unloaded"

# 合法转移表：只约束加载/初始化/卸载路径，调用路径不在其中
_ALLOWED = {
    DISCOVERED: {LOADED},
    LOADED: {INITIALIZED, FAILED, UNLOADING},
    INITIALIZED: {UNLOADING},
    FAILED: {UNLOADING, LOADED},   # 表面上允许“失败后重试”
    UNLOADING: {UNLOADED},
    UNLOADED: {LOADED},            # 表面上允许“卸载后重新加载”
}


class IllegalTransition(Exception):
    pass


def transition(plugin, target):
    """校验并执行状态转移。"""
    current = plugin.state
    allowed = _ALLOWED.get(current, set())
    if target not in allowed:
        raise IllegalTransition(f"{plugin.name}: {current} -> {target}")
    plugin.state = target
    return plugin
