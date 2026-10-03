"""体积对比与选择分布报告。

运行：python3 src/benchmark.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rowfilter as rf
import images

W, H, BPP = 64, 48, 3


def fmt_counts(counts, height):
    parts = []
    for f in rf.FILTERS:
        n = counts[f]
        parts.append("%s:%d(%.0f%%)" % (rf.FILTER_NAMES[f], n, 100.0 * n / height))
    return " ".join(parts)


def main():
    header = "%-22s %7s | %6s %6s %6s %6s %6s | %8s %6s | %s" % (
        "图像类型", "原始字节", "None", "Sub", "Up", "Avg", "Paeth",
        "逐行自适应", "最优", "选择分布(行数)")
    print(header)
    print("-" * len(header))

    for name, (raw, w, h, bpp) in images.all_types(W, H, BPP).items():
        sizes = {}
        for f in rf.FILTERS:
            sizes[f] = len(rf.encode(raw, w, h, bpp, force_filter=f))
        adaptive = len(rf.encode(raw, w, h, bpp))
        best = len(rf.encode_auto(raw, w, h, bpp))
        _, counts = rf.filter_distribution(raw, w, h, bpp)

        # “最优”综合了逐行自适应与 5 种单一滤波器，体积必不大于任一单一滤波器
        assert best <= min(sizes.values()), "最优体积不应超过任何单一滤波器"

        print("%-22s %7d | %6d %6d %6d %6d %6d | %8d %6d | %s" % (
            name, len(raw),
            sizes[0], sizes[1], sizes[2], sizes[3], sizes[4],
            adaptive, best, fmt_counts(counts, h)))

    print()
    print("说明：")
    print("- None/Sub/Up/Avg/Paeth 为整幅图强制使用该滤波器后的压缩体积（字节）。")
    print("- “逐行自适应”按每行有符号残差绝对值最小选滤波器；选错滤波器体积会明显变大，")
    print("  例如类照片用 None(8616) 与逐行自适应(5438) 相差约 58%。")
    print("- “最优”在逐行自适应与 5 种单一策略共 6 者中取最小，保证不逊于任一单一滤波器；")
    print("  个别高度冗余小图上，混合 filter type 字节会干扰 zlib 匹配，单一滤波器可能更小。")


if __name__ == "__main__":
    main()
