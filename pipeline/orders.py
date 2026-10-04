"""把阶段组装成订单处理流水线，并提供渲染最终输出的辅助函数。"""
from __future__ import annotations

from .effects import EffectSink
from .runner import Context, RetryPolicy, Runner, StageSpec
from . import stages

DEFAULT_RETRY = RetryPolicy(max_attempts=3)


def build_stages():
    """返回完整流水线的阶段列表，每个阶段都可单独替换/重试/跳过。"""
    return [
        StageSpec("parse", stages.parse_stage, retry=DEFAULT_RETRY),
        StageSpec("validate", stages.validate_stage, retry=DEFAULT_RETRY),
        StageSpec("enrich", stages.enrich_stage, retry=DEFAULT_RETRY),
        StageSpec("aggregate", stages.aggregate_stage, retry=DEFAULT_RETRY),
        StageSpec("emit_report", stages.emit_report_stage, retry=DEFAULT_RETRY),
        StageSpec(
            "emit_notify",
            stages.emit_notify_stage,
            when=lambda ctx: ctx.options.get("notify", True),
            retry=DEFAULT_RETRY,
        ),
    ]


def run_orders(lines, options=None, hooks=None, sink=None, stage_list=None):
    """跑完整流水线，返回 Context（含中间结果 results 与执行轨迹 trace）。"""
    ctx = Context(
        input=list(lines),
        options=dict(options or {}),
        sink=sink or EffectSink(),
    )
    Runner(stage_list or build_stages(), hooks=hooks).run(ctx)
    return ctx


def render_report(sink):
    """从副作用汇渲染 report.txt 内容（与遗留实现逐字节一致）。"""
    lines = [
        payload
        for key, payload in sink.items()
        if key.startswith("report:") or key.startswith("reject:")
    ]
    lines.append(sink.payload("summary"))
    return "\n".join(lines) + "\n"


def render_outbox(sink):
    """从副作用汇渲染 outbox.log 内容。"""
    lines = [
        payload
        for key, payload in sink.items()
        if key.startswith("notify:")
    ]
    return "\n".join(lines) + ("\n" if lines else "")
