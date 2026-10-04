"""teardown 很慢的插件：用于观察“卸载进行中被调用”。"""

import time


class Plugin:
    def __init__(self):
        self.name = "gamma"
        self.state = None
        self.conn = None
        self.teardown_started = False

    def init(self):
        self.conn = {"open": True, "last": None}

    def teardown(self):
        self.teardown_started = True
        self.conn = None       # 资源先释放……
        time.sleep(0.3)        # ……然后再慢慢收尾（模拟 flush / 网络等待）

    def work(self, value):
        self.conn["last"] = value
        return f"gamma-stored-{value}"
