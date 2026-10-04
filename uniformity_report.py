"""打印分桶均匀性报告：各目标比例下的实测比例与偏差。"""

import math

from gray_release import DEFAULT_BUCKETS, GrayRelease

SAMPLE_SIZE = 200_000
TARGETS = (0.1, 1, 5, 10, 25, 50, 90)


def main() -> None:
    users = [f"user-{i}" for i in range(SAMPLE_SIZE)]
    print(f"样本量: {SAMPLE_SIZE:,} 用户, 分桶数: {DEFAULT_BUCKETS:,} (SHA-256 取模)")
    print(f"{'目标比例':>10} {'实测比例':>10} {'偏差(pp)':>10} {'6σ容差(pp)':>12} {'结论':>6}")
    for target in TARGETS:
        gray = GrayRelease(ratio=target, salt="uniformity-check")
        hits = sum(1 for u in users if gray.is_gray(u))
        actual = hits / SAMPLE_SIZE * 100
        p = target / 100.0
        tolerance = 6 * math.sqrt(p * (1 - p) / SAMPLE_SIZE) * 100
        deviation = actual - target
        verdict = "通过" if abs(deviation) <= tolerance else "超限"
        print(f"{target:>9.1f}% {actual:>9.3f}% {deviation:>+10.4f} {tolerance:>12.4f} {verdict:>6}")


if __name__ == "__main__":
    main()
