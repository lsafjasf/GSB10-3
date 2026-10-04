"""演示：打印触发顺序数据（逻辑时间注入，输出完全确定）。"""

from timing_wheel import HierarchicalTimingWheel


def main():
    w = HierarchicalTimingWheel(base_interval=1, slots_per_layer=8, start_time=0)

    plan = [
        ("alpha",   30),
        ("beta",     5),
        ("gamma", 5000),   # 将被取消
        ("delta",   30),
        ("epsilon",  0),   # 立即超时
        ("zeta",    30),
        ("eta",  100000),  # 跨多层降级
    ]
    handles = {name: w.insert(delay, name) for name, delay in plan}
    handles["gamma"].cancel()

    print(f"{'fire_at':>8} {'deadline':>8} {'seq':>4}  payload")
    for t in [0, 5, 30, 5000, 100000]:
        for node in w.advance(t):
            print(f"{t:>8} {node.deadline:>8} {node.seq:>4}  {node.payload}")
    print(f"remaining pending: {len(w)}  (gamma 被取消，永不触发)")


if __name__ == "__main__":
    main()
