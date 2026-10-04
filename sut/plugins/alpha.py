"""正常插件：init 订阅事件，teardown 退订。

LOAD_COUNT 是模块级状态：只有模块代码被重新执行时才会复位。
"""

from pluginfw.hooks import BUS

LOAD_COUNT = 0


class Plugin:
    def __init__(self):
        self.name = "alpha"
        self.state = None
        self.ready = False
        self.received = []

    def init(self):
        global LOAD_COUNT
        LOAD_COUNT += 1
        self.ready = True
        BUS.subscribe("tick", self.on_tick)

    def teardown(self):
        BUS.unsubscribe("tick", self.on_tick)
        self.ready = False

    def on_tick(self, payload):
        self.received.append(payload)

    def work(self):
        if not self.ready:
            raise RuntimeError("alpha: work() 在 init 之前被调用")
        return "alpha-ok"
