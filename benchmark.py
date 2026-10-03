"""基准与演示：阈值提前终止 vs 完整计算；长文本近似匹配命中清单。

运行：python3 benchmark.py
输出：控制台打印，并写入 threshold_comparison.md 与 hit_list.md
"""

import random
import time

from edit_distance import Costs, distance, find_similar

ALPHABET = "acgt"


def mutate(rng, s, edits):
    """对 s 随机做 edits 次编辑（含相邻交换），得到相似串。"""
    s = list(s)
    for _ in range(edits):
        op = rng.randrange(4)
        if not s:
            s.insert(0, rng.choice(ALPHABET))
            continue
        i = rng.randrange(len(s))
        if op == 0:
            s.insert(i, rng.choice(ALPHABET))
        elif op == 1 and len(s) > 1:
            del s[i]
        elif op == 2:
            s[i] = rng.choice(ALPHABET)
        elif i + 1 < len(s):
            s[i], s[i + 1] = s[i + 1], s[i]
    return "".join(s)


def bench_threshold():
    rng = random.Random(2026)
    rows = []
    for n, edits in [(500, 25), (1000, 50), (2000, 100), (4000, 200)]:
        a = "".join(rng.choices(ALPHABET, k=n))
        b = mutate(rng, a, edits)
        true_d, full_cells = distance(a, b)
        t0 = time.perf_counter()
        distance(a, b)
        t_full = time.perf_counter() - t0
        for k in (true_d // 2, true_d, true_d * 2):
            t0 = time.perf_counter()
            d, cells = distance(a, b, threshold=k)
            t_thr = time.perf_counter() - t0
            rows.append((n, true_d, k,
                         "超出阈值→None" if d is None else str(d),
                         full_cells, cells,
                         f"{cells / full_cells:.1%}",
                         t_full * 1e3, t_thr * 1e3))
    return rows


def demo_hits():
    rng = random.Random(7)
    n = 100_000
    text = list("".join(rng.choices(ALPHABET, k=n)))
    pattern = "gattaca"
    plants = [
        (5_000, "gattaca", "完全匹配"),
        (20_000, "gattaga", "1 次替换"),
        (40_000, "gatatca", "1 次相邻交换 (ta->at)"),
        (60_000, "gatataca", "1 次插入"),
        (80_000, "gattaa", "1 次删除"),
    ]
    for pos, frag, _ in plants:
        text[pos:pos + len(frag)] = frag
    text = "".join(text)

    t0 = time.perf_counter()
    hits = find_similar(text, pattern, max_distance=1)
    elapsed = time.perf_counter() - t0

    planted = [h for h in hits if any(p <= h.start < p + 8 for p, _, _ in plants)]
    return text, pattern, hits, planted, plants, elapsed, n


def main():
    print("== 阈值提前终止 vs 完整计算 ==")
    rows = bench_threshold()
    header = ("串长", "真实距离", "阈值k", "阈值计算结果", "完整格子数",
              "实际格子数", "格子占比", "完整耗时ms", "阈值耗时ms")
    print(("{:>6} {:>8} {:>8} {:>14} {:>12} {:>12} {:>8} {:>10} {:>10}"
           ).format(*header))
    lines = []
    for r in rows:
        print("{:>6} {:>8} {:>8} {:>14} {:>12} {:>12} {:>8} {:>10.2f} {:>10.2f}"
              .format(*r))
        lines.append("| " + " | ".join(str(x) if not isinstance(x, float)
                                       else f"{x:.2f}" for x in r) + " |")

    with open("threshold_comparison.md", "w") as f:
        f.write("# 阈值提前终止 vs 完整计算对比\n\n")
        f.write("随机 DNA 串（字母表 acgt），真实距离由约 5% 的随机编辑产生；\n")
        f.write("格子数 = DP 实际计算的单元格数，占比越小说明剪枝越多。\n\n")
        f.write("| 串长 | 真实距离 | 阈值k | 阈值计算结果 | 完整格子数 | 实际格子数 "
                "| 格子占比 | 完整耗时ms | 阈值耗时ms |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        f.write("\n".join(lines) + "\n")
        f.write("\n结论：k = 真实距离时结果与完整计算一致；k 小于真实距离时返回 "
                "None 且只算少量格子；k 越小剪枝越狠。\n")

    print("\n== 长文本近似匹配 ==")
    text, pattern, hits, planted, plants, elapsed, n = demo_hits()
    print(f"文本长度 {n}，模式 {pattern!r}，max_distance=1，"
          f"命中 {len(hits)} 处，耗时 {elapsed:.2f}s")
    print("植入片段的命中情况：")
    for pos, frag, desc in plants:
        h = next((h for h in planted if pos <= h.start < pos + 8), None)
        status = (f"命中 [{h.start}, {h.end}) dist={h.distance} "
                  f"片段={text[h.start:h.end]!r}" if h else "未命中！")
        print(f"  植入@{pos} ({desc}): {status}")

    with open("hit_list.md", "w") as f:
        f.write("# 长文本近似匹配命中清单\n\n")
        f.write(f"- 文本长度：{n}（随机 acgt，另植入 5 个已知片段）\n")
        f.write(f"- 模式串：`{pattern}`，阈值 max_distance=1\n")
        f.write(f"- 命中总数：{len(hits)}，耗时 {elapsed:.2f}s\n\n")
        f.write("## 植入片段（ground truth）\n\n")
        f.write("| 植入位置 | 片段 | 说明 | 命中区间 | 距离 |\n|---|---|---|---|---|\n")
        for pos, frag, desc in plants:
            h = next((h for h in planted if pos <= h.start < pos + 8), None)
            f.write(f"| {pos} | `{frag}` | {desc} | "
                    f"[{h.start}, {h.end}) | {h.distance} |\n")
        f.write("\n## 全部命中（前 50 条）\n\n")
        f.write("| start | end | distance | 片段 |\n|---|---|---|---|\n")
        for h in hits[:50]:
            f.write(f"| {h.start} | {h.end} | {h.distance} | "
                    f"`{text[h.start:h.end]}` |\n")
        if len(hits) > 50:
            f.write(f"\n（其余 {len(hits) - 50} 条略）\n")


if __name__ == "__main__":
    main()
