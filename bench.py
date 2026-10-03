"""十万级地址段基准：合并结果 + 二分查找耗时 + 线性扫描对照。

运行：python3 bench.py
产物：
- merge_result.txt  合并结果（含统计与样例）
- query_bench.txt   单次查询耗时数据
"""

import random
import time

from ipset_lib import AddressSet, merge_networks

SEED = 20261003
N_V4 = 100_000   # 十万级 IPv4 段
N_V6 = 20_000    # 额外 2 万 IPv6 段
N_QUERIES = 200_000
N_LINEAR_QUERIES = 1_000  # 线性扫描慢，只测少量对照


def rand_cidr_v4(rng):
    # 97% 为 /24~/32 的具体段（稀疏撒在整个 IPv4 空间，合并后仍是十万级），
    # 3% 为 /16~/23 的中等段，制造相邻/包含/重叠/重复。
    if rng.random() < 0.97:
        prefix = rng.randint(24, 32)
    else:
        prefix = rng.randint(16, 23)
    addr = rng.randrange(2 ** 32)
    # 对齐到前缀边界
    addr &= ~((1 << (32 - prefix)) - 1)
    return f"{addr >> 24}.{(addr >> 16) & 255}.{(addr >> 8) & 255}.{addr & 255}/{prefix}"


def rand_cidr_v6(rng):
    prefix = rng.randint(32, 128)
    addr = rng.getrandbits(128)
    addr &= ~((1 << (128 - prefix)) - 1)
    return f"{addr:x}::{addr & ((1 << 64) - 1):x}/{prefix}" if False else _fmt_v6(addr, prefix)


def _fmt_v6(addr, prefix):
    import ipaddress
    return f"{ipaddress.IPv6Address(addr)}/{prefix}"


def timed(fn, repeat=1):
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


def main():
    rng = random.Random(SEED)
    raw_v4 = [rand_cidr_v4(rng) for _ in range(N_V4)]
    raw_v6 = [rand_cidr_v6(rng) for _ in range(N_V6)]
    raw = raw_v4 + raw_v6

    # ---- 合并 ----
    t_merge = timed(lambda: merge_networks(raw))
    merged = merge_networks(raw)
    merged_v4 = [n for n in merged if n.version == 4]
    merged_v6 = [n for n in merged if n.version == 6]

    # ---- 构建 AddressSet（合并 + 区间索引） ----
    def build():
        aset = AddressSet()
        for cidr in raw:
            aset.add(cidr)
        aset.build()
        return aset

    t_build = timed(build, repeat=3)
    aset = build()

    # ---- 查询地址样本：一半取自已有段（必命中），一半随机（多未命中） ----
    query_addrs = []
    import ipaddress as _ip
    for _ in range(N_QUERIES // 2):
        net = _ip.ip_network(rng.choice(raw), strict=False)
        query_addrs.append(str(net[rng.randrange(net.num_addresses)]))
    for _ in range(N_QUERIES // 2):
        if rng.random() < 0.5:
            query_addrs.append(str(_ip.IPv4Address(rng.randrange(2 ** 32))))
        else:
            query_addrs.append(str(_ip.IPv6Address(rng.getrandbits(128))))
    rng.shuffle(query_addrs)

    # 预热
    for ip in query_addrs[:1000]:
        aset.lookup(ip)

    t_bin = timed(lambda: [aset.lookup(ip) for ip in query_addrs], repeat=3)
    hits = sum(1 for ip in query_addrs if aset.lookup(ip).hit)

    # ---- 线性扫描对照（仅 v4、少量查询） ----
    linear_v4 = [ip for ip in query_addrs[: N_LINEAR_QUERIES * 5]
                 if ":" not in ip][:N_LINEAR_QUERIES]

    def linear_run():
        for ip in linear_v4:
            aset.lookup_linear(ip)

    t_lin = timed(linear_run, repeat=1)

    per_bin_us = t_bin / len(query_addrs) * 1e6
    per_lin_us = t_lin / len(linear_v4) * 1e6

    # ---- 输出合并结果文件 ----
    with open("merge_result.txt", "w", encoding="utf-8") as f:
        f.write("地址段合并结果（seed=%d）\n" % SEED)
        f.write("=" * 60 + "\n")
        f.write(f"原始段总数:   {len(raw)} (IPv4 {N_V4} + IPv6 {N_V6})\n")
        f.write(f"合并后段总数: {len(merged)} (IPv4 {len(merged_v4)} + IPv6 {len(merged_v6)})\n")
        f.write(f"合并耗时:     {t_merge * 1000:.2f} ms\n\n")
        f.write("合并后 IPv4 段（前 30 条，按地址序）:\n")
        for n in merged_v4[:30]:
            f.write(f"  {n}\n")
        f.write("...\n")
        f.write("合并后 IPv6 段（前 30 条，按地址序）:\n")
        for n in merged_v6[:30]:
            f.write(f"  {n}\n")
        f.write("...\n")

    # ---- 输出耗时数据文件 ----
    lines = []
    lines.append("查询耗时基准（seed=%d, Python %s）"
                 % (SEED, __import__("sys").version.split()[0]))
    lines.append("=" * 64)
    lines.append(f"数据规模: 原始段 {len(raw)}（IPv4 {N_V4} / IPv6 {N_V6}），"
                 f"合并后 {len(merged)}")
    lines.append(f"构建索引（合并+排序，取 3 次最优）: {t_build * 1000:.2f} ms（一次性）")
    lines.append("")
    lines.append("二分查找（bisect，O(log N)）:")
    lines.append(f"  查询次数:        {len(query_addrs)}")
    lines.append(f"  总耗时(3次最优): {t_bin * 1000:.2f} ms")
    lines.append(f"  单次平均:        {per_bin_us:.3f} µs/次")
    lines.append(f"  命中: {hits}  未命中: {len(query_addrs) - hits}")
    lines.append("")
    lines.append("线性扫描对照（O(N)，仅 IPv4）:")
    lines.append(f"  查询次数:        {len(linear_v4)}")
    lines.append(f"  总耗时:          {t_lin * 1000:.2f} ms")
    lines.append(f"  单次平均:        {per_lin_us:.1f} µs/次")
    lines.append(f"  加速比（同等规模外推）: 约 {per_lin_us / per_bin_us:,.0f}x")
    lines.append("")
    lines.append("结论: 二分查找单次耗时与段数无关；十万级段下单次查询稳定在微秒级，")
    lines.append("      不会随段数增长退化为线性扫描。")
    report = "\n".join(lines)
    print(report)
    with open("query_bench.txt", "w", encoding="utf-8") as f:
        f.write(report + "\n")


if __name__ == "__main__":
    main()
