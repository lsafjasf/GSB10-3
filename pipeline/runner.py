"""阶段运行器：显式传递中间结果、阶段边界钩子、按阶段重试。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import TransientError


@dataclass
class RetryPolicy:
    max_attempts: int = 1
    retry_on: tuple = (TransientError,)


@dataclass
class StageSpec:
    name: str
    func: object                       # fn(ctx) -> 中间结果
    when: object = None                # fn(ctx) -> bool，False 则跳过该阶段
    retry: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass
class Context:
    """在阶段之间显式传递的中间结果容器。"""
    input: object = None
    options: dict = field(default_factory=dict)
    sink: object = None
    results: dict = field(default_factory=dict)   # 阶段名 -> 该阶段的中间结果
    trace: list = field(default_factory=list)     # StageTrace 列表


@dataclass
class StageTrace:
    name: str
    status: str      # "ok" | "failed" | "skipped"
    attempts: int


class Hooks:
    """阶段边界观测钩子，默认全部空实现，按需子类化。"""

    def before_stage(self, name, ctx):
        pass

    def after_stage(self, name, ctx, result, attempts):
        pass

    def on_stage_error(self, name, ctx, exc, attempt):
        pass

    def on_stage_skipped(self, name, ctx):
        pass


class Runner:
    def __init__(self, stages, hooks=None):
        self.stages = stages
        self.hooks = hooks or Hooks()

    def run(self, ctx):
        for spec in self.stages:
            if spec.when is not None and not spec.when(ctx):
                ctx.trace.append(StageTrace(spec.name, "skipped", 0))
                self.hooks.on_stage_skipped(spec.name, ctx)
                continue

            self.hooks.before_stage(spec.name, ctx)
            attempt = 0
            while True:
                attempt += 1
                try:
                    result = spec.func(ctx)
                    break
                except spec.retry.retry_on as exc:
                    self.hooks.on_stage_error(spec.name, ctx, exc, attempt)
                    if attempt >= spec.retry.max_attempts:
                        ctx.trace.append(StageTrace(spec.name, "failed", attempt))
                        raise
            ctx.results[spec.name] = result
            ctx.trace.append(StageTrace(spec.name, "ok", attempt))
            self.hooks.after_stage(spec.name, ctx, result, attempt)
        return ctx
