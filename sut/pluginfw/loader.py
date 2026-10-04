"""插件发现与加载：扫描目录 -> import 模块 -> 实例化插件类。"""

import importlib.util
import os
import sys

from . import lifecycle
from .registry import REGISTRY


class PluginDescriptor:
    """发现阶段的插件描述符（此时还没有插件实例）。"""

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.state = lifecycle.DISCOVERED


def discover(plugin_dir):
    """扫描 plugin_dir 下的 *.py，返回 DISCOVERED 状态的描述符列表。"""
    found = []
    for fname in sorted(os.listdir(plugin_dir)):
        if fname.endswith(".py") and not fname.startswith("_"):
            name = fname[:-3]
            found.append(PluginDescriptor(name, os.path.join(plugin_dir, fname)))
    return found


def load(descriptor):
    """执行模块代码并实例化其中的 Plugin 类。

    模块被注入 sys.modules：之后同名加载直接命中缓存，模块代码不会重新执行。
    实例在 init 之前就被写入 REGISTRY —— 初始化在 manager.py 里才发生。
    """
    module_name = f"plugins.{descriptor.name}"
    if module_name in sys.modules:
        module = sys.modules[module_name]  # 命中缓存：模块级代码不会重新执行
    else:
        spec = importlib.util.spec_from_file_location(module_name, descriptor.path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    plugin = module.Plugin()
    plugin.name = descriptor.name
    plugin.state = lifecycle.LOADED  # 直接赋值，未经 lifecycle.transition() 校验
    REGISTRY.register(plugin)        # 未初始化的实例从此对 get()/call() 可见
    return plugin
