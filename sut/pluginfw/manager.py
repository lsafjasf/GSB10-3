"""插件管理器：编排 load / initialize / unload / call。"""

from . import lifecycle
from .errors import PluginInitError, PluginNotFound
from .loader import discover, load
from .registry import REGISTRY


class PluginManager:
    def __init__(self, plugin_dir):
        self.plugin_dir = plugin_dir

    def load_plugin(self, name):
        """发现 + 加载。重复调用不做去重，直接覆盖注册表中的同名条目。"""
        for desc in discover(self.plugin_dir):
            if desc.name == name:
                return load(desc)
        raise PluginNotFound(name)

    def initialize_plugin(self, name):
        plugin = REGISTRY.get(name)
        if plugin is None:
            raise PluginNotFound(name)
        try:
            plugin.init()
        except Exception as exc:
            lifecycle.transition(plugin, lifecycle.FAILED)  # 置 FAILED，但仍留在 REGISTRY
            raise PluginInitError(f"{name}: init failed: {exc}") from exc
        lifecycle.transition(plugin, lifecycle.INITIALIZED)
        return plugin

    def unload_plugin(self, name):
        plugin = REGISTRY.get(name)
        if plugin is None:
            raise PluginNotFound(name)
        lifecycle.transition(plugin, lifecycle.UNLOADING)
        plugin.teardown()       # teardown 执行期间插件仍在 REGISTRY 中
        REGISTRY.remove(name)
        lifecycle.transition(plugin, lifecycle.UNLOADED)
        # 注意：sys.modules 中的模块不清理，下次 load 命中 loader.py 的缓存分支

    def call(self, name, method, *args, **kwargs):
        """分发调用。只检查“在不在注册表”，不检查生命周期状态。"""
        plugin = REGISTRY.get(name)
        if plugin is None:
            raise PluginNotFound(name)
        return getattr(plugin, method)(*args, **kwargs)
