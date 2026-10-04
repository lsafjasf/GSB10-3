"""reprosched -- 可复现调度库（仅标准库）。

核心思想
--------
* 用户代码跑在真实线程上，但 Scheduler 任意时刻只把一个“虚拟线程”
  放到运行态（协作式交错），因此交错完全受控。
* 每个 SharedVar 读写、Mutex acquire/release 都是一个调度点。
  调度器在每个调度点选择下一个可运行的线程，选择序列即“交错序列”(trace)。
* 记录下来的 trace 可以用 ReplayPolicy 重放，得到完全相同的事件序列
  与最终状态（见 RunResult.events / final_state）。
* explore() 用无状态 DFS（CHESS 风格）枚举全部调度，统计暴露缺陷
  （断言失败 / 死锁 / 活锁）的调度数量。
"""

from __future__ import annotations

import json
import random
import threading

__all__ = [
    "Scheduler", "Mutex", "SharedVar", "RunResult", "ExploreStats",
    "FirstReadyPolicy", "RandomPolicy", "ReplayPolicy",
    "ReplayError", "run_program", "explore", "format_events", "to_json",
]


class ReplayError(Exception):
    """重放序列与程序实际可达的调度不一致时抛出。"""


class _Abort(Exception):
    """内部信号：调度器中止（死锁/活锁/重放错误）时让工作线程退出。"""


_LOCAL = threading.local()


def _current():
    try:
        return _LOCAL.vt
    except AttributeError:
        raise RuntimeError("调度原语只能在被 Scheduler 管理的线程中调用")


# ---------------------------------------------------------------------------
# 虚拟线程
# ---------------------------------------------------------------------------

class VThread:
    BOOT, READY, RUNNING, BLOCKED, DONE = "boot", "ready", "running", "blocked", "done"

    def __init__(self, sched, tid, fn):
        self.sched = sched
        self.tid = tid
        self.fn = fn
        self.status = self.BOOT
        self.error = None
        self._t = threading.Thread(target=self._bootstrap, name=tid, daemon=True)

    def start(self):
        self._t.start()

    def _bootstrap(self):
        _LOCAL.vt = self
        try:
            self.point("start")
            self.fn()
        except _Abort:
            pass
        except Exception as exc:  # 用户代码异常 => 记录为缺陷
            self.error = exc
        finally:
            with self.sched.cond:
                self.status = self.DONE
                self.sched.cond.notify_all()

    def point(self, event=None):
        """调度点：挂起自己，直到调度器选中。"""
        sched = self.sched
        with sched.cond:
            self.status = self.READY
            sched.cond.notify_all()
            while sched.turn is not self and not sched.aborted:
                sched.cond.wait()
            if sched.aborted:
                raise _Abort()
            sched.turn = None
            self.status = self.RUNNING
        if event is not None:
            sched.log(self, event)


# ---------------------------------------------------------------------------
# 同步原语（每次操作都是一个调度点）
# ---------------------------------------------------------------------------

class SharedVar:
    def __init__(self, sched, name, value):
        self.sched = sched
        self.name = name
        self._value = value

    def read(self):
        vt = _current()
        vt.point()
        value = self._value
        self.sched.log(vt, f"read {self.name} -> {value!r}")
        return value

    def write(self, value):
        vt = _current()
        vt.point()
        old = self._value
        self.sched.log(vt, f"write {self.name}: {old!r} -> {value!r}")
        self._value = value

    def peek(self):
        """非调度点读取，仅供程序结束后的断言使用。"""
        return self._value


class Mutex:
    def __init__(self, sched, name):
        self.sched = sched
        self.name = name
        self.owner = None
        self.waiters = []

    def acquire(self):
        vt = _current()
        vt.point(f"acquire({self.name}) try")
        s = self.sched
        with s.cond:
            if self.owner is None:
                self.owner = vt
                s.log(vt, f"acquire({self.name}) ok")
                return
            self.waiters.append(vt)
            vt.status = VThread.BLOCKED
            s.log(vt, f"acquire({self.name}) block (owner={self.owner.tid})")
            s.cond.notify_all()
            while s.turn is not vt and not s.aborted:
                s.cond.wait()
            if s.aborted:
                raise _Abort()
            s.turn = None
            vt.status = VThread.RUNNING
            s.log(vt, f"acquire({self.name}) ok (handoff)")

    def release(self):
        vt = _current()
        vt.point(f"release({self.name})")
        s = self.sched
        with s.cond:
            if self.owner is not vt:
                raise RuntimeError(f"线程 {vt.tid} 释放了不属于自己的锁 {self.name}")
            if self.waiters:
                nxt = self.waiters.pop(0)
                self.owner = nxt          # 所有权直接移交，避免惊群竞争
                nxt.status = VThread.READY
            else:
                self.owner = None
            s.cond.notify_all()


# ---------------------------------------------------------------------------
# 调度策略
# ---------------------------------------------------------------------------

class FirstReadyPolicy:
    """确定性默认策略：总是选线程号最小的可运行线程。"""

    def choose(self, enabled, sched):
        return enabled[0]


class RandomPolicy:
    def __init__(self, seed=None):
        self._rng = random.Random(seed)

    def choose(self, enabled, sched):
        return self._rng.choice(enabled)


class ReplayPolicy:
    """按记录的交错序列重放；序列耗尽或线程不可运行即报错。"""

    def __init__(self, trace):
        self.trace = list(trace)
        self.pos = 0

    def choose(self, enabled, sched):
        if self.pos >= len(self.trace):
            raise ReplayError(
                f"重放序列在第 {self.pos} 步耗尽，但程序仍需调度 "
                f"(enabled={[t.tid for t in enabled]})"
            )
        tid = self.trace[self.pos]
        self.pos += 1
        for t in enabled:
            if t.tid == tid:
                return t
        raise ReplayError(
            f"重放序列第 {self.pos - 1} 步要求线程 {tid}，"
            f"但它当前不可运行 (enabled={[t.tid for t in enabled]})"
        )


class _GuidedPolicy:
    """explore() 内部使用：先按 prefix 走，之后总选第 0 个，并记录每步的可选集。"""

    def __init__(self, prefix):
        self.prefix = prefix
        self.enabled_history = []  # 每一步可选的 tid 列表

    def choose(self, enabled, sched):
        tids = [t.tid for t in enabled]
        self.enabled_history.append(tids)
        depth = len(self.enabled_history) - 1
        idx = self.prefix[depth] if depth < len(self.prefix) else 0
        return enabled[idx]


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------

class Scheduler:
    def __init__(self, policy=None, max_steps=10000):
        self.cond = threading.Condition()
        self.turn = None
        self.aborted = False
        self.threads = []
        self.vars = {}
        self.mutexes = {}
        self.trace = []    # 交错序列：每一步被选中的 tid
        self.events = []   # 可观察事件：[{step, tid, event}, ...]
        self.policy = policy if policy is not None else FirstReadyPolicy()
        self.max_steps = max_steps

    # -- 程序构建 API --
    def spawn(self, fn, name=None):
        tid = name or f"T{len(self.threads) + 1}"
        vt = VThread(self, tid, fn)
        self.threads.append(vt)
        vt.start()
        return vt

    def var(self, name, value):
        v = SharedVar(self, name, value)
        self.vars[name] = v
        return v

    def mutex(self, name):
        m = Mutex(self, name)
        self.mutexes[name] = m
        return m

    def peek(self, name):
        return self.vars[name].peek()

    # -- 运行 --
    def log(self, vt, event):
        self.events.append({"step": len(self.events), "tid": vt.tid, "event": event})

    def _abort(self):
        self.aborted = True
        self.cond.notify_all()

    def run(self):
        """驱动所有线程直到结束；返回 'ok' | 'deadlock' | 'livelock'。"""
        while True:
            with self.cond:
                while any(t.status in (VThread.BOOT, VThread.RUNNING) for t in self.threads):
                    self.cond.wait()
                if all(t.status == VThread.DONE for t in self.threads):
                    return "ok"
                enabled = [t for t in self.threads if t.status == VThread.READY]
                if not enabled:
                    self._abort()  # 有线程永久阻塞在锁上
                    return "deadlock"
                if len(self.trace) >= self.max_steps:
                    self._abort()
                    return "livelock"
                try:
                    nxt = self.policy.choose(enabled, self)
                except Exception:
                    self._abort()
                    raise
                self.trace.append(nxt.tid)
                nxt.status = VThread.RUNNING
                self.turn = nxt
                self.cond.notify_all()


# ---------------------------------------------------------------------------
# 运行结果
# ---------------------------------------------------------------------------

class RunResult:
    def __init__(self, sched, outcome, check_error=None):
        self.outcome = outcome
        self.trace = list(sched.trace)
        self.events = list(sched.events)
        self.thread_errors = {t.tid: t.error for t in sched.threads if t.error is not None}
        self.check_error = check_error
        self.final_state = {name: v.peek() for name, v in sched.vars.items()}

    @property
    def bug(self):
        return self.outcome != "ok" or bool(self.thread_errors) or self.check_error is not None

    def signature(self):
        """缺陷签名：同一签名视为同一个缺陷。"""
        parts = [self.outcome]
        for tid, err in sorted(self.thread_errors.items()):
            parts.append(f"{tid}:{type(err).__name__}:{err}")
        if self.check_error is not None:
            parts.append(f"check:{self.check_error}")
        return " | ".join(parts)


def run_program(program, policy=None, check=None, max_steps=10000):
    """运行一次。program(sched) 负责 spawn 线程；check(sched) 在结束后断言。"""
    sched = Scheduler(policy=policy, max_steps=max_steps)
    program(sched)
    outcome = sched.run()
    check_error = None
    if outcome == "ok" and check is not None:
        try:
            check(sched)
        except Exception as exc:
            check_error = exc
    return RunResult(sched, outcome, check_error)


# ---------------------------------------------------------------------------
# 调度空间遍历
# ---------------------------------------------------------------------------

class ExploreStats:
    def __init__(self):
        self.schedules = 0          # 遍历的调度总数
        self.failing = 0            # 暴露缺陷的调度数
        self.signatures = {}        # 缺陷签名 -> [出现次数, 示例 RunResult]
        self.truncated = False

    @property
    def defects(self):
        return len(self.signatures)


def explore(program, check=None, max_steps=10000, max_schedules=None):
    """无状态 DFS 枚举全部调度（每个调度点枚举所有可运行线程）。"""
    stats = ExploreStats()
    prefix = []
    while True:
        policy = _GuidedPolicy(prefix)
        result = run_program(program, policy=policy, check=check, max_steps=max_steps)
        stats.schedules += 1
        if result.bug:
            stats.failing += 1
            entry = stats.signatures.setdefault(result.signature(), [0, result])
            entry[0] += 1
        # 回溯：找最深的有未尝试分支的调度点
        history = policy.enabled_history
        next_prefix = None
        for depth in range(len(history) - 1, -1, -1):
            chosen = prefix[depth] if depth < len(prefix) else 0
            if chosen + 1 < len(history[depth]):
                next_prefix = [(prefix[i] if i < len(prefix) else 0) for i in range(depth)]
                next_prefix.append(chosen + 1)
                break
        if next_prefix is None:
            break
        prefix = next_prefix
        if max_schedules is not None and stats.schedules >= max_schedules:
            stats.truncated = True
            break
    return stats


# ---------------------------------------------------------------------------
# 交错序列的输出格式
# ---------------------------------------------------------------------------

def format_events(result):
    """人类可读的交错序列：每行 `step  tid  event`。"""
    width = max((len(e["tid"]) for e in result.events), default=2)
    lines = [f"{'step':>4}  {'tid':<{width}}  event"]
    for e in result.events:
        lines.append(f"{e['step']:>4}  {e['tid']:<{width}}  {e['event']}")
    return "\n".join(lines)


def to_json(result, scenario=None):
    """机器可读的交错序列格式（可保存后用 ReplayPolicy 重放）。"""
    return json.dumps(
        {
            "scenario": scenario,
            "outcome": result.outcome,
            "bug": result.bug,
            "choices": result.trace,
            "events": result.events,
            "final_state": result.final_state,
            "thread_errors": {k: f"{type(v).__name__}: {v}" for k, v in result.thread_errors.items()},
            "check_error": str(result.check_error) if result.check_error else None,
        },
        ensure_ascii=False,
        indent=2,
    )
