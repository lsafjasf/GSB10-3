"""分阶段流水线运行器：钩子观测、单阶段重试、断点续跑、阶段跳过。"""
import json
import os


class PipelineError(Exception):
    def __init__(self, stage_name, attempts, cause):
        super().__init__(
            "stage %r failed after %d attempt(s): %s" % (stage_name, attempts, cause)
        )
        self.stage_name = stage_name
        self.attempts = attempts
        self.cause = cause


class Hook:
    """观测钩子基类。所有回调都是可选的，子类按需覆盖。"""

    def on_stage_start(self, stage_name, ctx):
        pass

    def on_stage_retry(self, stage_name, ctx, attempt, error):
        pass

    def on_stage_success(self, stage_name, ctx, attempts):
        pass

    def on_stage_skip(self, stage_name, ctx, reason):
        pass

    def on_stage_failure(self, stage_name, ctx, error):
        pass


class Stage:
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn


class Pipeline:
    def __init__(self, stages, hooks=(), max_attempts=3, checkpoint_path=None):
        self.stages = list(stages)
        self.hooks = list(hooks)
        self.max_attempts = max_attempts
        self.checkpoint_path = checkpoint_path
        self.state_path = (
            checkpoint_path + ".state.json" if checkpoint_path else None
        )

    def _notify(self, method, *args):
        for hook in self.hooks:
            getattr(hook, method)(*args)

    def _load_completed(self):
        completed = set()
        if self.checkpoint_path and os.path.exists(self.checkpoint_path):
            with open(self.checkpoint_path, encoding="utf-8") as fh:
                for line in fh:
                    name = line.strip()
                    if name:
                        completed.add(name)
        return completed

    def _mark_completed(self, stage_name, ctx):
        if not self.checkpoint_path:
            return
        with open(self.checkpoint_path, "a", encoding="utf-8") as fh:
            fh.write(stage_name + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path = self.state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(ctx.data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.state_path)

    def _restore_state(self, ctx):
        if not self.state_path or not os.path.exists(self.state_path):
            return
        with open(self.state_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        for key, value in saved.items():
            ctx.data.setdefault(key, value)

    def run(self, ctx, only=None, skip=()):
        """按顺序执行阶段。

        only: 只运行指定阶段（其余记为 not-selected 跳过）；
        skip: 显式跳过的阶段名；
        已在 checkpoint 中完成的阶段自动跳过（断点续跑），
        其中间结果从状态快照恢复（要求 ctx.data 可 JSON 序列化）。
        """
        self._restore_state(ctx)
        completed = self._load_completed()
        skip = set(skip)
        for stage in self.stages:
            if only is not None and stage.name not in only:
                self._notify("on_stage_skip", stage.name, ctx, "not-selected")
                continue
            if stage.name in skip:
                self._notify("on_stage_skip", stage.name, ctx, "skipped")
                continue
            if stage.name in completed:
                self._notify("on_stage_skip", stage.name, ctx, "completed")
                continue
            self._run_stage(stage, ctx)
        return ctx

    def _run_stage(self, stage, ctx):
        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            if attempt == 1:
                self._notify("on_stage_start", stage.name, ctx)
            else:
                self._notify("on_stage_retry", stage.name, ctx, attempt, last_error)
            try:
                stage.fn(ctx)
            except Exception as exc:
                last_error = exc
                continue
            self._mark_completed(stage.name, ctx)
            self._notify("on_stage_success", stage.name, ctx, attempt)
            return
        error = PipelineError(stage.name, self.max_attempts, last_error)
        self._notify("on_stage_failure", stage.name, ctx, error)
        raise error from last_error
