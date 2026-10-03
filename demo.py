"""分片重组演示：python3 demo.py

用注入的虚拟时钟演示：正常重组、乱序到达、重叠（先到为准）、永久缺失超时、
总量上界触发的 LRU 清退，并把清退记录写入 examples/eviction_records.json。
"""

import json
import os

from reassembly import Fragment, Reassembler


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def tick(self, dt=1.0):
        self.t += dt


def show(title):
    print("=" * 64)
    print(title)
    print("=" * 64)


def main():
    clk = FakeClock()
    reasm = Reassembler(timeout=10.0, max_total_bytes=32, time_func=clk)

    show("1) 正常重组（顺序到达）")
    offset = 0
    for data in (b"GET /index ", b"HTTP/1.1\r\n"):
        d = reasm.add(Fragment("req-1", offset, data))
        offset += len(data)
    d = reasm.add(Fragment("req-1", offset, b"", final=True))
    print(f"req-1 => {d.payload!r}（重叠丢弃 {d.overlap_bytes} 字节），缓冲用量 {reasm.used_bytes}")

    show("2) 乱序到达（尾分片最先到，中间空洞后补）")
    reasm.add(Fragment("req-2", 6, b"pkt!", final=True))
    print("t=0  先到尾分片 [6,10)，空洞 [0,6)，组暂存")
    d = reasm.add(Fragment("req-2", 0, b"hello "))
    print(f"t=0  头部补齐 => req-2 自动交付 {d.payload!r}（交付即释放缓冲）")

    show("3) 分片重叠：先到达的数据为准")
    reasm.add(Fragment("req-3", 0, b"AAAA"))
    print("先到 [0,4) = AAAA")
    d = reasm.add(Fragment("req-3", 2, b"BBBB", final=True))
    print("后到 [2,6) = BBBB：重叠区 [2,4) 的 BB 被丢弃，仅采纳 [4,6)")
    print(f"req-3 => {d.payload!r}（重叠丢弃 {d.overlap_bytes} 字节）")

    show("4) 永久缺失：10s 超时后丢弃并释放资源")
    reasm.add(Fragment("req-4", 0, b"only-head"))
    reasm.add(Fragment("req-4", 20, b"tail-here", final=True))  # 空洞 [9,20) 永不到达
    print(f"t={clk.t}  req-4 已收 18 字节，总长 29，空洞 11 字节；缓冲用量 {reasm.used_bytes}")
    clk.tick(10)
    print(f"t={clk.t}  达到判据 now >= 建组时刻 + timeout(=10s)，执行清扫")
    reasm.sweep()
    print(f"      清扫后缓冲用量 {reasm.used_bytes}，在途组数 {reasm.buffered_groups}")

    show("5) 总量上界 32 字节：按 LRU 整组清退")
    clk.t = 0
    print("t=0  放入 A(10B)、B(10B)、C(14B)；A 最久未更新")
    reasm.add(Fragment("A", 0, b"a" * 10))
    clk.tick(1)
    reasm.add(Fragment("B", 0, b"b" * 10))
    clk.tick(1)
    reasm.add(Fragment("C", 0, b"c" * 14))  # 30+14>32 -> 清退 A
    print(f"t=2  C 进入：清退 A；现用量 {reasm.used_bytes}")
    clk.tick(1)
    reasm.add(Fragment("B", 0, b"b" * 10))  # 重复分片刷新 B
    clk.tick(1)
    reasm.add(Fragment("D", 0, b"d" * 10))  # C 的 LRU 时间更旧 -> 清退 C
    print(f"t=4  D 进入：B 刚被刷新，清退 C；现用量 {reasm.used_bytes}")

    show("清退记录（eviction log）")
    records = [r.to_dict() for r in reasm.eviction_log]
    print(json.dumps(records, indent=2, ensure_ascii=False))

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "eviction_records.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(records, fp, indent=2, ensure_ascii=False)
    print(f"\n清退记录已写入 {path}")


if __name__ == "__main__":
    main()
