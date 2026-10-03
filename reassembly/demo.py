"""演示：正常/乱序/重叠重组 + 超时丢弃 + 容量清退记录。

运行：python3 -m reassembly.demo
"""

from reassembly.reassembler import Reassembler


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        print(f"  -- 时钟前进 {dt}s，now={self.t:.1f}s")


def show_evictions(rx, label):
    print(f"[{label}] 清退记录：")
    for rec in rx.eviction_log:
        print(f"  {rec}")
    if not rx.eviction_log:
        print("  （无）")


def main():
    clock = FakeClock()

    print("=== 1. 乱序 + 重叠重组（先到数据为准） ===")
    rx = Reassembler(max_total_bytes=1024, group_timeout=30.0, clock=clock)
    # 报文 "hello world!" 切成 3 片，乱序到达，且第 2 片与第 1 片重叠
    r = rx.add_fragment("msg-1", offset=6, data=b"world", more=True)   # 中间片先到
    print(f"  收到 offset=6  'world'  -> 完成? {r.reassembled is not None}, 缓冲 {rx.buffered_bytes}B")
    r = rx.add_fragment("msg-1", offset=11, data=b"!", more=False)      # 末片
    print(f"  收到 offset=11 '!'(末片) -> 完成? {r.reassembled is not None}, 缓冲 {rx.buffered_bytes}B")
    r = rx.add_fragment("msg-1", offset=0, data=b"hello ", more=True)   # 首片最后到
    print(f"  收到 offset=0  'hello ' -> 重组结果: {r.reassembled!r}, 缓冲释放为 {rx.buffered_bytes}B")

    print()
    print("=== 2. 重叠冲突样例：同一区域两次投递，先到为准 ===")
    rx.add_fragment("msg-2", offset=0, data=b"ABCDEF", more=True)       # 先到 "ABCDEF"
    r = rx.add_fragment("msg-2", offset=3, data=b"XYZW", more=False)    # 与 [3,6) 重叠
    print(f"  先到 [0,6)='ABCDEF'，后到 [3,7)='XYZW'（重叠部分被裁）")
    print(f"  重组结果: {r.reassembled!r}  （'DEF' 保留先到数据，仅采纳新字节 'W'）")

    print()
    print("=== 3. 永久缺失 + 超时丢弃 ===")
    rx2 = Reassembler(max_total_bytes=1024, group_timeout=10.0, clock=clock)
    rx2.add_fragment("msg-3", offset=0, data=b"AAAAA", more=True)
    rx2.add_fragment("msg-3", offset=10, data=b"BBBBB", more=False)
    print(f"  缺失 [5,10) 永远不到，空洞: {rx2.holes('msg-3')}, 缓冲 {rx2.buffered_bytes}B")
    clock.advance(10.1)  # 超过 group_timeout=10s（判据: now - first_seen >= 10）
    records = rx2.expire()
    print(f"  超时清理后: 缓冲 {rx2.buffered_bytes}B, 活跃组 {rx2.active_groups}")
    show_evictions(rx2, "超时")

    print()
    print("=== 4. 总量上界 + LRU 清退 ===")
    clock2 = FakeClock()
    rx3 = Reassembler(max_total_bytes=100, group_timeout=1000.0, clock=clock2)
    rx3.add_fragment("g1", 0, b"A" * 40, more=True)
    clock2.advance(1)
    rx3.add_fragment("g2", 0, b"B" * 40, more=True)
    clock2.advance(1)
    rx3.add_fragment("g1", 40, b"A" * 10, more=True)   # g1 刷新活跃度
    clock2.advance(1)
    print(f"  上界 100B，当前占用 {rx3.buffered_bytes}B；再注入 30B 触发清退")
    rx3.add_fragment("g3", 0, b"C" * 30, more=True)
    print(f"  清退后: 占用 {rx3.buffered_bytes}B, 活跃组 {rx3.active_groups}")
    show_evictions(rx3, "容量")

    print()
    print("=== 5. 单分片超过总上界 -> 拒绝 ===")
    r = rx3.add_fragment("huge", 0, b"X" * 200, more=True)
    print(f"  accepted={r.accepted}, 缓冲 {rx3.buffered_bytes}B")
    print(f"  最新记录: {rx3.eviction_log[-1]}")


if __name__ == "__main__":
    main()
