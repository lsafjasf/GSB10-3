"""cancelscope.py —— 超时与级联取消的结构化并发库（仅标准库）。

核心概念
--------
* CancelToken : 取消令牌，构成父子树。父令牌 cancel() 后，整棵子树
                收到取消信号（cancel() 幂等，原因首次生效）。
* Scope       : 任务作用域（nursery）。spawn() 产生的子任务共享该
                作用域的令牌；作用域为空时任务才算结束。
* Task        : 基于生成器的协程，yield Sleep(n) 模拟工作；取消时调度器
                在 yield 点向其 throw(Cancelled)。任务可捕获 Cancelled
                做收尾（cleanup），收尾期间允许继续 yield Sleep。
* Clock       : 可注入时钟。ManualClock 由调度器自动推进，测试完全
                确定；SystemClock 使用 time.monotonic 真实运行。

失败策略
--------
* FAIL_FAST : 任一子任务失败立即取消兄弟任务与全部后代，作用域提前
              结束，父任务收到 ChildFailed（单错误）/ ChildErrors（多错误）。
* COLLECT  : 不取消兄弟任务，等待全部结束后汇总错误抛出 ChildErrors。

任务自身超时且未捕获 Cancelled 时，转化为 ScopeTimeout 失败并按作用域
策略向上传播；任务可以捕获 Cancelled 返回降级结果来吞掉超时。
"""
from __future__ import annotations

import inspect
import itertools
import time
from typing import Any, Callable, List, Optional, Tuple

# ---------------------------------------------------------------- 时钟


class SystemClock:
    """真实时钟，run() 中以 time.sleep 等待下一个事件。"""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


class ManualClock:
    """确定性测试时钟，只能前进；调度器自动推进到下一个事件时刻。"""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("clock cannot go backwards")
        self._now += seconds

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)


# ---------------------------------------------------------------- 错误


class Cancelled(Exception):
    """任务所在作用域被取消时，调度器在 yield 点注入的异常。"""


class ScopeTimeout(TimeoutError):
    """任务自身 deadline 到期且未吞掉取消。"""


class ChildFailed(Exception):
    """作用域中单个子任务失败，向上传播给作用域拥有者。"""

    def __init__(self, task_name: str, error: BaseException) -> None:
        super().__init__(f"child task {task_name!r} failed: {error!r}")
        self.task_name = task_name
        self.error = error


class ChildErrors(Exception):
    """作用域中多个子任务失败（COLLECT 或同一时刻并发失败）。"""

    def __init__(self, errors: List[Tuple[str, BaseException]]) -> None:
        self.errors = list(errors)
        detail = "; ".join(f"{name}: {err!r}" for name, err in self.errors)
        super().__init__(f"{len(self.errors)} child task error(s): {detail}")


# 失败策略
FAIL_FAST = "fail_fast"
COLLECT = "collect"

_ACTIVE_STATES = ("running", "waiting_children")


# ---------------------------------------------------------------- 原语


class Sleep:
    """生成器 yield 的唯一合法命令：等待 duration 秒。"""

    __slots__ = ("duration",)

    def __init__(self, duration: float) -> None:
        if duration < 0:
            raise ValueError(f"sleep duration must be >= 0, got {duration}")
        self.duration = float(duration)


class CancelToken:
    """取消令牌树。父令牌取消 => 全部子令牌级联取消。"""

    def __init__(self, parent: Optional["CancelToken"] = None) -> None:
        self.parent = parent
        self.children: List["CancelToken"] = []
        self.cancelled = False
        self.reason: Optional[str] = None
        if parent is not None:
            parent.children.append(self)

    def cancel(self, reason: str = "parent") -> None:
        if self.cancelled:
            return  # 幂等；保留首次原因
        self.cancelled = True
        self.reason = reason
        for child in list(self.children):
            child.cancel(reason)  # 级联


class Task:
    _ids = itertools.count()

    def __init__(self, sched: "Scheduler", scope: "Scope",
                 fn: Callable[..., Any], name: Optional[str],
                 timeout: Optional[float]) -> None:
        self.id = next(Task._ids)
        self.sched = sched
        self.scope = scope                 # 所属作用域
        self.name = name or f"task-{self.id}"
        self.token = CancelToken(scope.token)
        self.child_scope = Scope(sched, self.token, scope.policy, owner=self)
        self.pending_outcome: Optional[Tuple[str, Any]] = None
        gen_or_value = fn(self.child_scope)
        if inspect.isgenerator(gen_or_value):
            self.gen = gen_or_value
            self.wake_at: Optional[float] = sched.clock.now()
            self.state = "running"
        else:
            # 非生成器函数：同步立即完成
            self.gen = None
            self.wake_at = None
            self.pending_outcome = ("done", gen_or_value)
            self.state = "waiting_children"  # 等待 _reap 收尾
        self.deadline: Optional[float] = (
            None if timeout is None else sched.clock.now() + float(timeout)
        )
        self.cancel_delivered = False
        self.timed_out = False
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.started_at = sched.clock.now()
        self.finished_at: Optional[float] = None

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE_STATES

    @property
    def cancelling(self) -> bool:
        """已收到取消信号、生成器仍在运行（通常在做收尾）。"""
        return self.state == "running" and self.cancel_delivered


class Scope:
    def __init__(self, sched: "Scheduler", token: CancelToken,
                 policy: str, owner: Optional[Task] = None) -> None:
        self.sched = sched
        self.token = token
        self.policy = policy
        self.owner = owner
        self.tasks: List[Task] = []
        self.errors: List[Tuple[str, BaseException]] = []

    def spawn(self, fn: Callable[..., Any], name: Optional[str] = None,
              timeout: Optional[float] = None,
              policy: Optional[str] = None) -> Task:
        """在本作用域启动子任务。作用域已取消时禁止再派生。"""
        if self.token.cancelled:
            raise Cancelled(
                f"cannot spawn {name!r}: scope already cancelled")
        task = Task(self.sched, self, fn, name, timeout)
        if policy is not None:
            task.child_scope.policy = policy
        self.tasks.append(task)
        self.sched._register(task)
        return task

    def active_tasks(self) -> List[Task]:
        return [t for t in self.tasks if t.active]

    def uncancelled_tasks(self) -> List[Task]:
        return [t for t in self.tasks if not t.token.cancelled]


# ---------------------------------------------------------------- 调度器


class Scheduler:
    def __init__(self, clock: Optional[Any] = None) -> None:
        self.clock = clock or SystemClock()
        self.tasks: List[Task] = []
        self.root_scope: Optional[Scope] = None
        self.root_task: Optional[Task] = None

    @property
    def active_count(self) -> int:
        return sum(1 for t in self.tasks if t.active)

    @property
    def uncancelled_count(self) -> int:
        return sum(1 for t in self.tasks if not t.token.cancelled)

    def _register(self, task: Task) -> None:
        self.tasks.append(task)

    def run(self, root_fn: Callable[..., Any], timeout: Optional[float] = None,
            policy: str = FAIL_FAST) -> Any:
        root_token = CancelToken()
        self.root_scope = Scope(self, root_token, policy)
        self.root_task = self.root_scope.spawn(root_fn, name="root",
                                               timeout=timeout)
        while self.active_count:
            self._enforce_deadlines()
            progressed = self._step_ready()
            self._reap()
            if self.active_count == 0:
                break
            if progressed:
                continue  # 可能有新派生任务或新取消，立即再处理一轮
            self._advance_clock()
        return self._outcome()

    # -- 内部 -------------------------------------------------------------

    def _enforce_deadlines(self) -> None:
        now = self.clock.now()
        for task in self.tasks:
            if (task.active and task.deadline is not None
                    and now >= task.deadline and not task.token.cancelled):
                task.timed_out = True
                task.token.cancel("timeout")  # 级联到所有后代

    def _step_ready(self) -> bool:
        now = self.clock.now()

        def is_ready(task: Task) -> bool:
            if task.state != "running" or task.gen is None:
                return False
            if task.token.cancelled and not task.cancel_delivered:
                return True
            return task.wake_at is not None and task.wake_at <= now

        # 同一时刻就绪的任务按 LIFO（后注册先执行）：子任务先于父任务
        # 完成，父任务唤醒时能立即读到子任务结果。
        ready = sorted((t for t in self.tasks if is_ready(t)),
                       key=lambda t: (t.wake_at if t.wake_at is not None
                                      else now, -t.id))
        progressed = bool(ready)
        for task in ready:
            try:
                if task.token.cancelled and not task.cancel_delivered:
                    task.cancel_delivered = True
                    command = task.gen.throw(
                        Cancelled(f"cancelled ({task.token.reason})"))
                else:
                    command = next(task.gen)
            except StopIteration as stop:
                self._settle(task, "done", stop.value)
            except Cancelled:
                self._settle(task, "cancelled", None)
            except BaseException as exc:  # noqa: BLE001 - 任务内任何异常都要捕获
                self._settle(task, "failed", exc)
            else:
                if not isinstance(command, Sleep):
                    self._settle(task, "failed", TypeError(
                        f"task {task.name!r} yielded unsupported command "
                        f"{command!r}, expected Sleep(...)"))
                else:
                    task.wake_at = now + command.duration
        return progressed

    def _settle(self, task: Task, kind: str, value: Any) -> None:
        task.pending_outcome = (kind, value)
        if task.child_scope.active_tasks():
            # 结构化并发：生成器结束后仍需等待子任务结束
            task.state = "waiting_children"
        else:
            self._finalize(task)

    def _reap(self) -> None:
        changed = True
        while changed:
            changed = False
            for task in self.tasks:
                if (task.state == "waiting_children"
                        and not task.child_scope.active_tasks()):
                    self._finalize(task)
                    changed = True

    def _finalize(self, task: Task) -> None:
        kind, value = task.pending_outcome
        # 自身超时：未在收尾中吞掉取消 => 转化为 ScopeTimeout 失败。
        # 若任务体捕获了 Cancelled 后正常返回（cancel_delivered），
        # 视为显式降级，不视为失败。
        if task.timed_out and kind != "failed":
            if kind == "cancelled" or not task.cancel_delivered:
                kind = "failed"
                value = ScopeTimeout(
                    f"task {task.name!r} exceeded its deadline")
        # 子作用域错误向上传播（失败不得被静默吞掉）
        if kind != "failed" and task.child_scope.errors:
            errors = task.child_scope.errors
            if len(errors) == 1:
                name, err = errors[0]
                value = ChildFailed(name, err)
            else:
                value = ChildErrors(errors)
            kind = "failed"

        task.state = kind  # done / failed / cancelled
        task.finished_at = self.clock.now()
        task.result = value if kind == "done" else None
        task.error = value if kind == "failed" else None

        if kind == "failed":
            task.scope.errors.append((task.name, value))
            task.token.cancel("fail_fast")  # 取消失败任务的后代
            if task.scope.policy == FAIL_FAST:
                task.scope.token.cancel("fail_fast")  # 取消兄弟任务

    def _advance_clock(self) -> None:
        now = self.clock.now()
        events: List[float] = []
        for task in self.tasks:
            if task.state == "running" and task.wake_at is not None:
                events.append(task.wake_at)
            if task.active and task.deadline is not None:
                events.append(task.deadline)
        future = [e for e in events if e > now]
        if not future:
            raise RuntimeError(
                f"deadlock: {self.active_count} active task(s) but no "
                "pending timer events")
        self.clock.sleep(min(future) - now)

    def _outcome(self) -> Any:
        root = self.root_task
        assert root is not None and self.root_scope is not None
        if self.root_scope.errors:
            raise self.root_scope.errors[0][1]
        if root.state == "cancelled":
            raise Cancelled(f"root cancelled ({root.token.reason})")
        return root.result


def run(root_fn: Callable[..., Any], timeout: Optional[float] = None,
        policy: str = FAIL_FAST, clock: Optional[Any] = None) -> Any:
    """便捷入口。"""
    return Scheduler(clock).run(root_fn, timeout=timeout, policy=policy)
