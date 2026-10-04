"""全局插件注册表：框架对外暴露的“插件清单”事实来源之一。"""


class Registry:
    def __init__(self):
        self._plugins = {}

    def register(self, plugin):
        self._plugins[plugin.name] = plugin

    def remove(self, name):
        self._plugins.pop(name, None)

    def get(self, name):
        return self._plugins.get(name)

    def names(self):
        return sorted(self._plugins)


# 进程级单例：所有 PluginManager 实例共享
REGISTRY = Registry()
