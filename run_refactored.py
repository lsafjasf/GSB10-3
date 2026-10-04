"""用法: python3 run_refactored.py <输入csv> <输出目录> [--no-notify]"""
import os
import sys

from pipeline.orders import render_outbox, render_report, run_orders


class StderrHooks:
    """演示用观测钩子：把阶段边界事件打到 stderr。"""

    def before_stage(self, name, ctx):
        print(f"[hook] -> {name}", file=sys.stderr)

    def after_stage(self, name, ctx, result, attempts):
        print(f"[hook] <- {name} ok attempts={attempts}", file=sys.stderr)

    def on_stage_error(self, name, ctx, exc, attempt):
        print(f"[hook] !! {name} attempt={attempt} error={exc}", file=sys.stderr)

    def on_stage_skipped(self, name, ctx):
        print(f"[hook] -- {name} skipped", file=sys.stderr)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    input_path, out_dir = args
    options = {"notify": "--no-notify" not in sys.argv}

    with open(input_path, "r", encoding="utf-8") as fh:
        ctx = run_orders(fh.readlines(), options=options, hooks=StderrHooks())

    report_text = render_report(ctx.sink)
    outbox_text = render_outbox(ctx.sink)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(report_text)
    with open(os.path.join(out_dir, "outbox.log"), "w", encoding="utf-8") as fh:
        fh.write(outbox_text)
    print(report_text, end="")


if __name__ == "__main__":
    main()
