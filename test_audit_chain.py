"""audit_chain 自测：篡改定位、区间/全量一致性、删除检测、边界用例。"""

import random
import unittest

import audit_chain
from audit_chain import AuditLog, Commitment


def build_log(n: int) -> AuditLog:
    log = AuditLog()
    for i in range(n):
        log.append({"op": "write", "key": f"k{i}", "amount": i * 7, "user": "u%03d" % i})
    return log


def tamper_record(log: AuditLog, index: int) -> None:
    """模拟攻击者改动已存储的记录内容。"""
    log._envs[index]["record"]["amount"] = -1


def delete_entries(log: AuditLog, index: int, count: int = 1) -> None:
    """模拟攻击者删除已存储的日志条目。"""
    del log._envs[index:index + count]
    del log._chain[index:index + count]


class TestCleanLog(unittest.TestCase):
    def test_full_and_ranges_pass(self):
        log = build_log(300)
        c = log.commitment()
        self.assertTrue(log.verify_full(c).ok)
        rng = random.Random(0)
        for _ in range(200):
            s = rng.randrange(0, 300)
            e = rng.randrange(s, 301)
            r = log.verify_range(s, e, c)
            self.assertTrue(r.ok, r)

    def test_every_inclusion_proof(self):
        for n in list(range(1, 18)) + [100]:
            log = build_log(n)
            c = log.commitment()
            for i in range(n):
                proof = log.inclusion_proof(i)
                self.assertTrue(
                    AuditLog.verify_inclusion(log._chain[i].hex(), proof, c),
                    f"n={n} i={i}")

    def test_commitment_json_roundtrip(self):
        c = build_log(10).commitment()
        self.assertEqual(Commitment.from_json(c.to_json()), c)


class TestSingleTamper(unittest.TestCase):
    def test_locate_single_modified_record(self):
        n, t = 300, 137
        log = build_log(n)
        c = log.commitment()
        tamper_record(log, t)
        full = log.verify_full(c)
        self.assertFalse(full.ok)
        self.assertEqual(full.reason, "chain_mismatch")
        self.assertEqual(full.first_mismatch, t)  # 精确定位到被改记录

    def test_range_full_consistency(self):
        """区间校验与全量校验结果一致：区间含篡改点则失败且定位相同。"""
        n, t = 300, 137
        log = build_log(n)
        c = log.commitment()
        tamper_record(log, t)
        full = log.verify_full(c)
        whole = log.verify_range(0, n, c)
        self.assertEqual((whole.ok, whole.reason, whole.first_mismatch),
                         (full.ok, full.reason, full.first_mismatch))
        rng = random.Random(1)
        for _ in range(300):
            s = rng.randrange(0, n)
            e = rng.randrange(s, n + 1)
            r = log.verify_range(s, e, c)
            expected_ok = not (s <= t < e)
            self.assertEqual(r.ok, expected_ok, f"[{s},{e}) {r}")
            if not r.ok:
                self.assertEqual(r.first_mismatch, t)

    def test_tamper_first_and_last(self):
        for t in (0, 199):
            log = build_log(200)
            c = log.commitment()
            tamper_record(log, t)
            full = log.verify_full(c)
            self.assertFalse(full.ok)
            self.assertEqual(full.first_mismatch, t)


class TestConsecutiveTamper(unittest.TestCase):
    def test_locate_first_of_run(self):
        n = 300
        log = build_log(n)
        c = log.commitment()
        for t in range(100, 110):  # 连续改 10 条
            tamper_record(log, t)
        full = log.verify_full(c)
        self.assertFalse(full.ok)
        self.assertEqual(full.first_mismatch, 100)  # 定位到连续篡改段起点
        # 覆盖部分篡改段的区间：定位到区间内首个被改记录
        r = log.verify_range(105, 120, c)
        self.assertFalse(r.ok)
        self.assertEqual(r.first_mismatch, 105)
        # 不覆盖篡改段的区间仍然通过
        self.assertTrue(log.verify_range(0, 100, c).ok)
        self.assertTrue(log.verify_range(110, 300, c).ok)


class TestTailDeletion(unittest.TestCase):
    def test_tail_delete_detected(self):
        log = build_log(300)
        c = log.commitment()
        delete_entries(log, 290, 10)  # 删掉末尾 10 条
        full = log.verify_full(c)
        self.assertFalse(full.ok)
        self.assertEqual(full.reason, "records_missing")
        self.assertEqual(full.first_mismatch, 290)  # 缺口位置
        # 未越界的区间仍通过；越界区间报缺失
        self.assertTrue(log.verify_range(0, 290, c).ok)
        r = log.verify_range(280, 300, c)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "records_missing")
        self.assertEqual(r.first_mismatch, 290)

    def test_delete_everything(self):
        log = build_log(50)
        c = log.commitment()
        delete_entries(log, 0, 50)
        full = log.verify_full(c)
        self.assertFalse(full.ok)
        self.assertEqual(full.first_mismatch, 0)


class TestMiddleDeletion(unittest.TestCase):
    def test_gap_located(self):
        n, d = 300, 150
        log = build_log(n)
        c = log.commitment()
        delete_entries(log, d)
        full = log.verify_full(c)
        self.assertFalse(full.ok)
        self.assertEqual(full.reason, "sequence_gap")
        self.assertEqual(full.first_mismatch, d)  # 精确指出缺口位置

    def test_range_full_consistency(self):
        """区间与全量一致：缺口之后的任何区间都无法通过校验。"""
        n, d = 300, 150
        log = build_log(n)
        c = log.commitment()
        delete_entries(log, d)
        full = log.verify_full(c)
        whole = log.verify_range(0, len(log), c)
        self.assertEqual((whole.ok, whole.reason, whole.first_mismatch),
                         (full.ok, full.reason, full.first_mismatch))
        rng = random.Random(2)
        for _ in range(300):
            s = rng.randrange(0, len(log))
            e = rng.randrange(s, len(log) + 1)
            r = log.verify_range(s, e, c)
            expected_ok = (e <= d)  # 缺口前的区间不受影响
            self.assertEqual(r.ok, expected_ok, f"[{s},{e}) {r}")
            if not r.ok:
                # s<=d：重放到缺口处报 sequence_gap；s>d：左边界已对不上承诺
                self.assertEqual(r.first_mismatch, d if s <= d else s - 1)


class TestStoredChainHashTamper(unittest.TestCase):
    def test_modified_chain_hash_fails_boundary_proof(self):
        log = build_log(100)
        c = log.commitment()
        log._chain[50] = b"\xff" * 32  # 攻击者直接改存储的链哈希
        r = log.verify_range(0, 100, c)
        self.assertFalse(r.ok)
        self.assertEqual(r.first_mismatch, 50)


class TestEdgeCases(unittest.TestCase):
    def test_empty_log(self):
        log = AuditLog()
        c = log.commitment()
        self.assertEqual(c.size, 0)
        self.assertTrue(log.verify_full(c).ok)
        self.assertTrue(log.verify_range(0, 0, c).ok)

    def test_single_record(self):
        log = build_log(1)
        c = log.commitment()
        self.assertTrue(log.verify_full(c).ok)
        tamper_record(log, 0)
        full = log.verify_full(c)
        self.assertFalse(full.ok)
        self.assertEqual(full.first_mismatch, 0)

    def test_single_element_ranges(self):
        log = build_log(64)
        c = log.commitment()
        for i in range(64):
            self.assertTrue(log.verify_range(i, i + 1, c).ok)

    def test_invalid_range_raises(self):
        log = build_log(10)
        c = log.commitment()
        with self.assertRaises(ValueError):
            log.verify_range(5, 3, c)
        with self.assertRaises(ValueError):
            log.verify_range(-1, 3, c)

    def test_range_beyond_commitment(self):
        log = build_log(10)
        c = log.commitment()
        r = log.verify_range(0, 11, c)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "range_out_of_commitment")

    def test_wrong_commitment_rejected(self):
        log_a, log_b = build_log(50), build_log(50)
        log_b.append({"op": "extra"})
        r = log_a.verify_full(log_b.commitment())
        self.assertFalse(r.ok)


class TestNoFullRecompute(unittest.TestCase):
    def test_range_verify_is_sublinear(self):
        """区间校验的哈希运算量远小于全量校验（不重算全链）。"""
        n = 4096
        log = build_log(n)
        c = log.commitment()

        class Counter:
            def __init__(self):
                self.n = 0
        counter = Counter()
        real_hash = audit_chain._hash

        def counting(data):
            counter.n += 1
            return real_hash(data)

        audit_chain._hash = counting
        try:
            counter.n = 0
            self.assertTrue(log.verify_range(1000, 1016, c).ok)
            range_cost = counter.n
            counter.n = 0
            self.assertTrue(log.verify_full(c).ok)
            full_cost = counter.n
        finally:
            audit_chain._hash = real_hash

        # 16 条区间 ≈ 2*16 + 2*log2(4096)=56 次；全量 ≈ 2*4096 次
        self.assertLess(range_cost, 100)
        self.assertGreater(full_cost, 8000)
        self.assertLess(range_cost * 50, full_cost)


if __name__ == "__main__":
    unittest.main(verbosity=2)
