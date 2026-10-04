"""可注入的模拟时钟，用于确定性测试与长时间运行模拟。"""


class FakeClock:
    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt

    def set(self, t):
        self.now = float(t)
