"""副作用汇：所有落盘/外发动作都经过它，用幂等键去重。

阶段重试时整个阶段函数会重新执行，但同一个幂等键只会真正生效一次，
因此"重试不得重复产生副作用"由这里保证，并且可以被断言。
"""
from __future__ import annotations


class EffectSink:
    def __init__(self):
        self._applied = {}       # key -> payload，保持插入序
        self.mutation_log = []   # 每次真正生效的 key，用于幂等断言

    def apply(self, key, payload):
        """应用一个副作用。已存在的 key 直接跳过（要求 payload 一致）。"""
        if key in self._applied:
            existing = self._applied[key]
            if existing != payload:
                raise AssertionError(
                    f"幂等键 {key!r} 冲突: {existing!r} != {payload!r}"
                )
            return False
        self._applied[key] = payload
        self.mutation_log.append(key)
        return True

    def keys(self):
        return list(self._applied)

    def payload(self, key):
        return self._applied[key]

    def items(self):
        return list(self._applied.items())

    def snapshot(self):
        return dict(self._applied)

    def assert_no_duplicate_effects(self):
        """幂等断言：没有任何一个副作用键被真正生效超过一次。"""
        assert len(self.mutation_log) == len(set(self.mutation_log)), (
            f"副作用被重复执行: {self.mutation_log}"
        )


def assert_idempotent(sink, stage_fn, ctx):
    """幂等断言：把同一个阶段再跑一遍，副作用集合必须保持不变。"""
    before = sink.snapshot()
    stage_fn(ctx)
    after = sink.snapshot()
    sink.assert_no_duplicate_effects()
    assert before == after, "阶段重跑产生了新的副作用，不满足幂等"
