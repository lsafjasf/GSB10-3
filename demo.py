"""demo.py —— 父超时 -> 级联取消 -> 资源释放 的可观测演示（真实时钟）。"""
from cancelscope import (Cancelled, ManualClock, Scheduler, ScopeTimeout,
                         Sleep, SystemClock)


def scenario(clock, label):
    sched = Scheduler(clock)
    log = []

    def root(scope):
        def make(i):
            def worker(s):
                log.append(("start", i, clock.now(), sched.active_count))
                try:
                    yield Sleep(100)  # 模拟长耗时后台任务
                except Cancelled:
                    log.append(("cancelled", i, clock.now(),
                                sched.active_count))
                    raise
            return worker
        for i in range(3):
            scope.spawn(make(i), name=f"worker-{i}")
        yield Sleep(100)

    try:
        sched.run(root, timeout=0.5 if isinstance(clock, SystemClock) else 5)
    except ScopeTimeout:
        pass

    peak = max(e[3] for e in log)
    cancel_at = next(e[2] for e in log if e[0] == "cancelled")
    print(f"[{label}]")
    print(f"  取消前活动任务数 : {peak}  (root + 3 workers)")
    print(f"  取消信号发出时刻 : t={cancel_at}")
    print(f"  收到取消的任务数 : {sum(1 for e in log if e[0] == 'cancelled')}")
    print(f"  取消后活动任务数 : {sched.active_count}")
    print(f"  未取消子任务数   : {sched.uncancelled_count}")
    assert sched.active_count == 0
    assert sched.uncancelled_count == 0


if __name__ == "__main__":
    scenario(ManualClock(), "ManualClock 虚拟时间")
    scenario(SystemClock(), "SystemClock 真实时间")
