"""重构后版本的命令行入口。

示例:
  python3 run_pipeline.py in.csv out.txt --audit audit.log --run-id run-1
  python3 run_pipeline.py in.csv out.txt --audit audit.log --run-id run-1 \
      --only load,normalize        # 只跑部分阶段
  python3 run_pipeline.py in.csv out.txt --audit audit.log --run-id run-1 \
      --skip audit                 # 跳过指定阶段
  python3 run_pipeline.py in.csv out.txt --audit audit.log --run-id run-1 \
      --checkpoint cp.log          # 中途失败后重跑，已完成阶段自动跳过
"""
import argparse
import sys

from pipeline import Context, EffectStore, Hook, Pipeline, STAGE_NAMES, default_stages


class LoggingHook(Hook):
    def __init__(self, stream):
        self.stream = stream

    def _log(self, message):
        self.stream.write(message + "\n")
        self.stream.flush()

    def on_stage_start(self, stage_name, ctx):
        self._log("[stage] start: %s" % stage_name)

    def on_stage_retry(self, stage_name, ctx, attempt, error):
        self._log("[stage] retry: %s (attempt=%d, error=%s)" % (stage_name, attempt, error))

    def on_stage_success(self, stage_name, ctx, attempts):
        self._log("[stage] success: %s (attempts=%d)" % (stage_name, attempts))

    def on_stage_skip(self, stage_name, ctx, reason):
        self._log("[stage] skip: %s (reason=%s)" % (stage_name, reason))

    def on_stage_failure(self, stage_name, ctx, error):
        self._log("[stage] failure: %s (after %d attempts)" % (stage_name, error.attempts))


def build_pipeline(max_attempts, checkpoint_path):
    return Pipeline(
        default_stages(),
        hooks=[LoggingHook(sys.stderr)],
        max_attempts=max_attempts,
        checkpoint_path=checkpoint_path,
    )


def main():
    parser = argparse.ArgumentParser(description="refactored staged pipeline")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--journal", default=None, help="effect journal path")
    parser.add_argument("--checkpoint", default=None, help="checkpoint path for resume")
    parser.add_argument("--only", default=None, help="comma-separated stage names to run")
    parser.add_argument("--skip", default="", help="comma-separated stage names to skip")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()

    if args.only:
        only = set(args.only.split(","))
        unknown = only - set(STAGE_NAMES)
        if unknown:
            parser.error("unknown stages in --only: %s" % ",".join(sorted(unknown)))
    else:
        only = None
    skip = {name for name in args.skip.split(",") if name}
    unknown = skip - set(STAGE_NAMES)
    if unknown:
        parser.error("unknown stages in --skip: %s" % ",".join(sorted(unknown)))

    journal_path = args.journal or args.output + ".effects.journal"
    ctx = Context(
        data={
            "input_path": args.input,
            "output_path": args.output,
            "audit_path": args.audit,
            "run_id": args.run_id,
        },
        effects=EffectStore(journal_path),
    )
    pipe = build_pipeline(args.max_attempts, args.checkpoint)
    pipe.run(ctx, only=only, skip=skip)


if __name__ == "__main__":
    main()
