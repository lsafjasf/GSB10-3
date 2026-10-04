"""init 首次失败、再次即成功的插件（模拟瞬时故障）。"""

INIT_ATTEMPTS = 0


class Plugin:
    def __init__(self):
        self.name = "delta"
        self.state = None
        self.ready = False

    def init(self):
        global INIT_ATTEMPTS
        INIT_ATTEMPTS += 1
        if INIT_ATTEMPTS == 1:
            raise RuntimeError("delta: 首次初始化瞬时失败")
        self.ready = True  # 第二次会真正初始化成功

    def teardown(self):
        self.ready = False

    def work(self):
        return "delta-ok" if self.ready else "delta-before-init"
