"""init 必然失败的插件（模拟配置缺失）。"""


class Plugin:
    def __init__(self):
        self.name = "beta"
        self.state = None
        # 注意：ready 只在 init 成功时才赋值，失败实例上没有该属性

    def init(self):
        raise RuntimeError("beta: 缺少必需配置，初始化失败")

    def teardown(self):
        pass

    def work(self):
        return f"beta-ok ready={self.ready}"
