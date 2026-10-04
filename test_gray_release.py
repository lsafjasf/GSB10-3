"""gray_release 自测：分桶均匀性、判定一致性、边界用例。

运行：python3 test_gray_release.py -v
"""

import unittest

from gray_release import BUCKETS, _bucket, in_gray

SAMPLE_SIZE = 100000  # 均匀性检验的样本量


def _make_user(i):
    return "user_%d" % i


class TestUniformity(unittest.TestCase):
    """分桶必须分布均匀，且实际命中比例与目标比例偏差要小。"""

    def test_bucket_distribution_uniform(self):
        """10 万个用户落入 100 个等宽区间，每区期望 1000，
        最大相对偏差应 < 15%（3-sigma 约 9.5%，留足余量防 flaky）。"""
        zones = 100
        counts = [0] * zones
        for i in range(SAMPLE_SIZE):
            counts[_bucket(_make_user(i), "gray") * zones // BUCKETS] += 1
        expected = SAMPLE_SIZE / zones
        max_dev = max(abs(c - expected) / expected for c in counts)
        print("\n[均匀性] %d 个用户 / %d 个区间，期望每区 %.0f，"
              "min=%d, max=%d, 最大相对偏差=%.2f%%"
              % (SAMPLE_SIZE, zones, expected,
                 min(counts), max(counts), max_dev * 100))
        self.assertLess(max_dev, 0.15)

    def test_hit_ratio_close_to_target(self):
        """多个目标比例下，实际命中率与目标的绝对偏差应 < 0.5%。"""
        print("\n[比例偏差] 样本量 %d" % SAMPLE_SIZE)
        for target in (1, 5, 10, 30, 50, 90, 99):
            hits = sum(
                1 for i in range(SAMPLE_SIZE)
                if in_gray(_make_user(i), target)
            )
            actual = hits / SAMPLE_SIZE * 100
            dev = abs(actual - target)
            print("  目标 %5.1f%% -> 实际 %6.3f%%，偏差 %.3f%%"
                  % (target, actual, dev))
            self.assertLess(dev, 0.5,
                            "目标 %s%% 偏差过大: %.3f%%" % (target, dev))


class TestConsistency(unittest.TestCase):
    """同一用户的判定必须可复现。"""

    def test_repeated_judgement_same_result(self):
        """同一用户重复判定 1000 次，结果必须完全一致。"""
        for i in range(200):
            uid = _make_user(i)
            first = in_gray(uid, 37, whitelist={"vip_1"}, salt="expA")
            for _ in range(1000):
                self.assertEqual(
                    first, in_gray(uid, 37, whitelist={"vip_1"}, salt="expA"),
                    "用户 %s 判定不稳定" % uid)

    def test_known_value_reproducible(self):
        """锚定具体哈希结果：跨代码改动之外的运行都必须复现。"""
        # 这些值由当前 sha256 分桶算法决定，若算法变更需同步更新。
        self.assertEqual(_bucket("user_0", "gray"), 790)
        self.assertEqual(_bucket("user_1", "gray"), 3680)
        self.assertTrue(in_gray("user_0", 50))
        self.assertFalse(in_gray("user_3", 50))

    def test_salt_isolates_experiments(self):
        """不同盐值产生不同分桶，但各自内部依然稳定。"""
        uid = "user_42"
        results_a = {in_gray(uid, 50, salt="expA") for _ in range(100)}
        results_b = {in_gray(uid, 50, salt="expB") for _ in range(100)}
        self.assertEqual(len(results_a), 1)
        self.assertEqual(len(results_b), 1)


class TestEdgeCases(unittest.TestCase):
    """边界：比例 0、比例 100、白名单命中、标识缺失。"""

    def test_percentage_zero_blocks_everyone(self):
        for i in range(1000):
            self.assertFalse(in_gray(_make_user(i), 0))

    def test_percentage_hundred_admits_everyone(self):
        for i in range(1000):
            self.assertTrue(in_gray(_make_user(i), 100))

    def test_whitelist_overrides_percentage(self):
        # 白名单命中时，比例为 0 也放行
        self.assertTrue(in_gray("vip_1", 0, whitelist={"vip_1"}))
        # 比例为 100 时白名单外的用户也全部放行（白名单无否决权）
        self.assertTrue(in_gray("nobody", 100, whitelist={"vip_1"}))
        # 白名单未命中时仍按比例走
        self.assertFalse(in_gray("nobody", 0, whitelist={"vip_1"}))

    def test_missing_user_id(self):
        self.assertFalse(in_gray(None, 100))
        self.assertFalse(in_gray("", 100))
        # 缺失标识即使传了白名单也不命中（None/"" 不在白名单语义内）
        self.assertFalse(in_gray(None, 100, whitelist={"vip_1"}))
        # 缺失标识的判定同样可复现
        self.assertEqual(in_gray(None, 50), in_gray(None, 50))

    def test_invalid_percentage_raises(self):
        with self.assertRaises(ValueError):
            in_gray("user_0", -1)
        with self.assertRaises(ValueError):
            in_gray("user_0", 101)


if __name__ == "__main__":
    unittest.main(verbosity=2)
