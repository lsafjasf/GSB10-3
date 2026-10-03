"""基准与数据生成脚本。

- 生成十万级地址段数据集（IPv4 + IPv6，含相同/包含/相邻关系）；
- 执行合并，校验合并不变量（有序、互斥、不相邻、覆盖一致、优先级正确）；
- 写出合并结果（data/merged_v4.tsv, data/merged_v6.tsv, data/merge_demo.txt）；
- 在 1万 / 5万 / 全量（约10万）规模下测量单次查询耗时；
- 用线性扫描抽样交叉验证查询正确性；
- 汇总写出 data/benchmark_report.json。

运行：python3 bench.py
"""

import ipaddress
import json
import os
import random
import time
from collections import Counter

from ipsetx import IPSet, MergedSegment

SEED = 20261003
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

V4_BASE_COUNT = 95_000
V4_DUP_COUNT = 3_000
V4_ENCLOSING_COUNT = 100
V6_BASE_COUNT = 20_000
V6_DUP_COUNT = 500
V6_ENCLOSING_COUNT = 20
QUERY_COUNT = 20_000


def generate_dataset(rng):
    """生成带优先级的地址段数据集，返回 (entries, 说明)。"""
    entries = []

    # IPv4：在 1.0.0.0 - 149.255.255.255 内随机 /24
    v4_bases = []
    for _ in range(V4_BASE_COUNT):
        base = rng.randint(0x01000000, 0x95FFFF00) & 0xFFFFFF00
        v4_bases.append(base)
        entries.append((str(ipaddress.IPv4Address(base)) + "/24", rng.randint(1, 1000)))
    # 完全相同：复制部分段，给不同优先级
    for _ in range(V4_DUP_COUNT):
        base = rng.choice(v4_bases)
        entries.append((str(ipaddress.IPv4Address(base)) + "/24", rng.randint(1, 1000)))
    # 包含：取若干 /24 的 /16 父段
    for _ in range(V4_ENCLOSING_COUNT):
        base = rng.choice(v4_bases) & 0xFFFF0000
        entries.append((str(ipaddress.IPv4Address(base)) + "/16", rng.randint(1, 1000)))

    # IPv6：在 2001:db8::/35 内随机 /64
    v6_prefix = int(ipaddress.IPv6Address("2001:db8::"))
    v6_bases = []
    for _ in range(V6_BASE_COUNT):
        base = v6_prefix + (rng.randrange(1 << 29) << 64)
        v6_bases.append(base)
        entries.append((str(ipaddress.IPv6Address(base)) + "/64", rng.randint(1, 1000)))
    for _ in range(V6_DUP_COUNT):
        base = rng.choice(v6_bases)
        entries.append((str(ipaddress.IPv6Address(base)) + "/64", rng.randint(1, 1000)))
    for _ in range(V6_ENCLOSING_COUNT):
        net = ipaddress.ip_network(
            f"{ipaddress.IPv6Address(rng.choice(v6_bases))}/48", strict=False
        )
        entries.append((str(net), rng.randint(1, 1000)))

    rng.shuffle(entries)
    desc = {
        "ipv4_base_/24": V4_BASE_COUNT,
        "ipv4_duplicates": V4_DUP_COUNT,
        "ipv4_enclosing_/16": V4_ENCLOSING_COUNT,
        "ipv6_base_/64": V6_BASE_COUNT,
        "ipv6_duplicates": V6_DUP_COUNT,
        "ipv6_enclosing_/48": V6_ENCLOSING_COUNT,
        "total_raw_entries": len(entries),
    }
    return entries, desc


def verify_merge(entries, ipset):
    """校验合并不变量，返回检查结果字典（全部应为 True）。"""
    v4_raw = sorted(
        (int(n.network_address), int(n.broadcast_address), p)
        for n, p in ((ipaddress.ip_network(c, strict=False), p) for c, p in entries)
        if n.version == 4
    )
    segs = ipset.segments_v4()
    starts = [int(s.network.network_address) for s in segs]
    ends = [int(s.network.broadcast_address) for s in segs]

    # 1) 有序、互不重叠，且是“最小 CIDR 覆盖”：
    #    相邻但因边界不对齐无法聚成单个 CIDR 的段允许存在（如 /23+/24），
    #    但整体必须与标准库 collapse_addresses 的规范结果完全一致。
    collapsed = list(ipaddress.collapse_addresses(s.network for s in segs))
    ordered_disjoint = (
        starts == sorted(starts)
        and all(starts[i] > ends[i - 1] for i in range(1, len(starts)))
        and [str(n) for n in collapsed] == [str(s.network) for s in segs]
    )

    # 2) 覆盖一致：合并前后的地址并集完全相同
    def union_intervals(intervals):
        out = []
        for s, e in intervals:
            if out and s <= out[-1][1] + 1:
                if e > out[-1][1]:
                    out[-1][1] = e
            else:
                out.append([s, e])
        return out

    raw_union = union_intervals((s, e) for s, e, _ in v4_raw)
    merged_union = union_intervals(zip(starts, ends))
    coverage_equal = raw_union == merged_union

    # 3) 优先级正确：每个合并段的优先级 == 所属连通分量内原始段的最高优先级
    prio_ok = True
    raw_iter = iter(v4_raw)
    raw = next(raw_iter, None)
    for (cu, cv), (mu, mv) in zip(raw_union, merged_union):
        assert (cu, cv) == (mu, mv)
    comp_min = []
    idx = 0
    for cu, cv in raw_union:
        best = None
        while idx < len(v4_raw) and v4_raw[idx][0] <= cv:
            p = v4_raw[idx][2]
            best = p if best is None or p < best else best
            idx += 1
        comp_min.append(best)
    comp_iter = iter(zip(raw_union, comp_min))
    comp = next(comp_iter, None)
    for s in segs:
        st = int(s.network.network_address)
        while comp is not None and st > comp[0][1]:
            comp = next(comp_iter, None)
        if comp is None or s.priority != comp[1]:
            prio_ok = False
            break

    return {
        "minimal_canonical_cidr_cover": ordered_disjoint,
        "coverage_equal_to_raw_union": coverage_equal,
        "priority_is_min_of_merged_group": prio_ok,
    }


def make_queries(rng, segments, version, count):
    """80% 命中（落在随机合并段内）+ 20% 未命中。"""
    hits, misses = [], []
    n_hit = int(count * 0.8)
    for _ in range(n_hit):
        seg = rng.choice(segments)
        lo = int(seg.network.network_address)
        hi = int(seg.network.broadcast_address)
        hits.append(lo + rng.randrange(hi - lo + 1))
    for _ in range(count - n_hit):
        if version == 4:
            # 200.0.0.0/8 不在数据集范围 1.0.0.0-149.255.255.255 内
            misses.append(0xC8000000 + rng.randrange(0x01000000))
        else:
            # 2001:db9:: 不在数据集范围 2001:db8::/35 内
            misses.append(int(ipaddress.IPv6Address("2001:db9::")) + rng.randrange(1 << 64))
    queries = hits + misses
    rng.shuffle(queries)
    return queries


def time_queries(ipset, queries, version, use_full_api=False):
    addr_cls = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
    addrs = [addr_cls(v) for v in queries]
    fn = ipset.query if use_full_api else ipset.match
    samples = []
    t0 = time.perf_counter()
    for a in addrs:
        s = time.perf_counter()
        fn(a)
        samples.append(time.perf_counter() - s)
    total = time.perf_counter() - t0
    samples.sort()
    n = len(samples)
    return {
        "queries": n,
        "total_ms": round(total * 1e3, 2),
        "avg_us": round(total / n * 1e6, 3),
        "min_us": round(samples[0] * 1e6, 3),
        "p50_us": round(samples[n // 2] * 1e6, 3),
        "p99_us": round(samples[int(n * 0.99)] * 1e6, 3),
        "max_us": round(samples[-1] * 1e6, 3),
    }


def write_merge_demo(path):
    cases = [
        ("单段", [("10.1.0.0/24", 10)]),
        ("相邻（对齐，聚合成单个 CIDR）", [("10.1.0.0/24", 10), ("10.1.1.0/24", 10)]),
        ("相邻（非对齐，最少 CIDR 覆盖）",
         [("10.2.0.0/24", 5), ("10.2.1.0/24", 5), ("10.2.2.0/24", 5)]),
        ("完全相同（去重，取最高优先级）",
         [("10.3.0.0/24", 8), ("10.3.0.0/24", 3)]),
        ("包含（小段被吸收，保留组内最高优先级）",
         [("10.4.0.0/16", 20), ("10.4.5.0/24", 7)]),
        ("跨版本混合（v4/v6 分别合并，互不混算）",
         [("10.5.0.0/24", 1), ("2001:db8::/64", 2), ("2001:db8:0:1::/64", 2)]),
        ("链式相邻（多段连成一片）",
         [("10.6.0.0/24", 4), ("10.6.1.0/24", 4), ("10.6.2.0/24", 4), ("10.6.3.0/24", 4)]),
    ]
    lines = ["合并演示：输入 -> 合并结果（优先级：数值越小越高）", ""]
    for title, entries in cases:
        lines.append(f"## {title}")
        for cidr, prio in entries:
            lines.append(f"  输入  {cidr:<22} priority={prio}")
        for seg in IPSet(entries).segments:
            lines.append(f"  输出  {seg.cidr:<22} priority={seg.priority}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    rng = random.Random(SEED)
    os.makedirs(DATA_DIR, exist_ok=True)

    print("== 生成数据集 ==")
    entries, desc = generate_dataset(rng)
    print(json.dumps(desc, ensure_ascii=False))

    print("== 合并 ==")
    t0 = time.perf_counter()
    ipset = IPSet(entries)
    build_ms = (time.perf_counter() - t0) * 1e3
    stats = ipset.stats()
    print(f"构建+合并耗时 {build_ms:.1f} ms, 合并结果: {stats}")

    print("== 校验合并不变量 ==")
    checks = verify_merge(entries, ipset)
    print(json.dumps(checks, ensure_ascii=False))
    assert all(checks.values()), "合并不变量校验失败"

    v4_segments = ipset.segments_v4()
    v6_segments = ipset.segments_v6()

    print("== 写出合并结果 ==")
    for name, segs in (("merged_v4.tsv", v4_segments), ("merged_v6.tsv", v6_segments)):
        with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
            f.write("cidr\tpriority\n")
            for s in segs:
                f.write(f"{s.cidr}\t{s.priority}\n")
    write_merge_demo(os.path.join(DATA_DIR, "merge_demo.txt"))

    print("== 查询正确性抽样交叉验证（对照线性扫描） ==")
    sample = [rng.randint(0x01000000, 0xC9000000) for _ in range(300)]
    t0 = time.perf_counter()
    for value in sample:
        expected = None
        for s in v4_segments:
            net = s.network
            if int(net.network_address) <= value <= int(net.broadcast_address):
                expected = (str(net), s.priority)
                break
        got = ipset.match(ipaddress.IPv4Address(value))
        assert got == expected, f"不一致: {value:#x} got={got} expected={expected}"
    crosscheck_ms = (time.perf_counter() - t0) * 1e3
    print(f"300 个随机地址全部一致（线性扫描对照耗时 {crosscheck_ms:.0f} ms，仅供对照）")

    print("== 查询耗时 ==")
    timing = {}
    # IPv4：1万 / 5万 / 全量 三种规模
    full_v4 = len(v4_segments)
    for scale in (10_000, 50_000, full_v4):
        sub = IPSet.from_segments(v4_segments[:scale])
        queries = make_queries(rng, v4_segments[:scale], 4, QUERY_COUNT)
        timing[f"ipv4_{scale}_segments"] = time_queries(sub, queries, 4)
        print(f"IPv4 {scale:>7} 段: {timing[f'ipv4_{scale}_segments']}")
    # 全量下完整 query() 接口（构造 QueryResult）的开销
    queries = make_queries(rng, v4_segments, 4, QUERY_COUNT)
    timing[f"ipv4_{full_v4}_segments_full_api"] = time_queries(ipset, queries, 4, use_full_api=True)
    print(f"IPv4 {full_v4:>7} 段(query接口): {timing[f'ipv4_{full_v4}_segments_full_api']}")
    # IPv6 全量
    queries6 = make_queries(rng, v6_segments, 6, QUERY_COUNT)
    timing[f"ipv6_{len(v6_segments)}_segments"] = time_queries(ipset, queries6, 6)
    print(f"IPv6 {len(v6_segments):>7} 段: {timing[f'ipv6_{len(v6_segments)}_segments']}")

    report = {
        "seed": SEED,
        "python": os.popen("python3 -V").read().strip(),
        "dataset": desc,
        "merge": {
            "build_and_merge_ms": round(build_ms, 1),
            **stats,
            "invariant_checks": checks,
        },
        "query_correctness_crosscheck": {
            "method": "二分索引 vs 线性扫描，随机抽样 300 个 IPv4 地址",
            "mismatches": 0,
        },
        "query_timing": timing,
        "notes": [
            "查询为有序区间二分查找 O(log n)，不随段数线性退化",
            "每次查询 80% 命中 + 20% 未命中，耗时为单次查询微秒数",
            "优先级约定：数值越小越高，合并段取组内最高优先级",
        ],
    }
    with open(os.path.join(DATA_DIR, "benchmark_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"报告已写入 {os.path.join(DATA_DIR, 'benchmark_report.json')}")


if __name__ == "__main__":
    main()
