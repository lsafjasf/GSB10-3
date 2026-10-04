"""gray_release 自测：边界用例 + 一致性断言 + 分桶均匀性。

运行：
    python3 test_gray_release.py          # 跑全部测试
    python3 test_gray_release.py -v       # 查看均匀性偏差明细
    python3 uniformity_report.py          # 单独打印均匀性报告
"""

import math
import unittest

from gray_release import GrayRelease

SAMPLE_SIZE = 200_000


def make_users(n: int) -> list:
    return [f"user-{i}" for i in range(n)]


def gray_ratio(users, ratio, salt="uniformity-check"):
    gray = GrayRelease(ratio=ratio, salt=salt)
    hits = sum(1 for u in users if gray.is_gray(u))
    return hits / len(users) * 100.0


class TestEdgeCases(unittest.TestCase):
    def test_ratio_zero_blocks_everyone(self):
        gray = GrayRelease(ratio=0)
        self.assertFalse(any(gray.is_gray(u) for u in make_users(10_000)))

    def test_ratio_hundred_allows_everyone(self):
        gray = GrayRelease(ratio=100)
        self.assertTrue(all(gray.is_gray(u) for u in make_users(10_000)))

    def test_whitelist_hit(self):
        gray = GrayRelease(ratio=0, whitelist=["vip-001", "vip-002"])
        self.assertTrue(gray.is_gray("vip-001"))
        self.assertTrue(gray.is_gray("vip-002"))
        self.assertFalse(gray.is_gray("user-1"))

    def test_whitelist_overrides_ratio(self):
        # 白名单优先级高于比例：即使比例为 0，白名单用户仍放量
        gray = GrayRelease(ratio=0, whitelist=["vip-001"])
        self.assertTrue(gray.is_gray("vip-001"))
        # 非白名单用户即使在桶内阈值外也不受影响（ratio=0 时无人按比例放量）
        self.assertFalse(gray.is_gray("user-1"))

    def test_missing_user_id_never_gray(self):
        for ratio in (0, 50, 100):
            gray = GrayRelease(ratio=ratio, whitelist=["vip-001"])
            self.assertFalse(gray.is_gray(None))
            self.assertFalse(gray.is_gray(""))
            self.assertFalse(gray.is_gray("   "))

    def test_invalid_ratio_rejected(self):
        for bad in (-1, 100.01, "50", None, True):
            with self.assertRaises((ValueError, TypeError)):
                GrayRelease(ratio=bad)


class TestConsistency(unittest.TestCase):
    def test_same_user_same_result_repeatedly(self):
        gray = GrayRelease(ratio=37.5, salt="consistency")
        for user in make_users(1_000):
            first = gray.is_gray(user)
            for _ in range(100):
                self.assertEqual(first, gray.is_gray(user), user)

    def test_same_result_across_instances(self):
        # 模拟多实例/多进程部署：相同配置的不同实例判定必须一致
        users = make_users(10_000)
        instance_a = GrayRelease(ratio=37.5, salt="consistency")
        instance_b = GrayRelease(ratio=37.5, salt="consistency")
        for user in users:
            self.assertEqual(instance_a.is_gray(user), instance_b.is_gray(user), user)
            self.assertEqual(instance_a.bucket_of(user), instance_b.bucket_of(user), user)

    def test_monotonic_rollout(self):
        # 比例上调只应新增放量用户，不应把已放量用户踢回旧版本
        users = make_users(50_000)
        low = GrayRelease(ratio=10, salt="rollout")
        high = GrayRelease(ratio=30, salt="rollout")
        for user in users:
            if low.is_gray(user):
                self.assertTrue(high.is_gray(user), user)


class TestUniformity(unittest.TestCase):
    """均匀性：实测比例与目标比例的偏差必须落在 6σ 统计容差内。"""

    def test_bucket_distribution_matches_target(self):
        users = make_users(SAMPLE_SIZE)
        for target in (1, 5, 10, 25, 50):
            actual = gray_ratio(users, target)
            p = target / 100.0
            sigma = math.sqrt(p * (1 - p) / SAMPLE_SIZE) * 100
            tolerance = 6 * sigma
            deviation = abs(actual - target)
            self.assertLessEqual(
                deviation,
                tolerance,
                f"目标 {target}% 实测 {actual:.4f}% 偏差 {deviation:.4f}pp 超出 6σ 容差 {tolerance:.4f}pp",
            )

    def test_chi_square_goodness_of_fit(self):
        # 卡方拟合优度检验（df=1，显著性水平 0.01 的临界值为 6.635）
        users = make_users(SAMPLE_SIZE)
        gray = GrayRelease(ratio=50, salt="uniformity-check")
        hits = sum(1 for u in users if gray.is_gray(u))
        expected = SAMPLE_SIZE / 2
        chi2 = (hits - expected) ** 2 / expected + (
            (SAMPLE_SIZE - hits) - expected
        ) ** 2 / expected
        self.assertLess(chi2, 6.635, f"卡方统计量 {chi2:.4f} 超出临界值 6.635")


if __name__ == "__main__":
    unittest.main()
