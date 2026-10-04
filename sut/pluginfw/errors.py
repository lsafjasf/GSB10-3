class PluginError(Exception):
    """插件框架统一异常基类"""


class PluginNotFound(PluginError):
    pass


class PluginInitError(PluginError):
    pass
