"""阶段之间显式传递的上下文：中间结果放在 data 里，副作用走 effects。"""
from .effects import EffectStore


class Context:
    def __init__(self, data=None, effects=None):
        self.data = dict(data or {})
        self.effects = effects if effects is not None else EffectStore()
