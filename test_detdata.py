"""detdata 自测：确定性、约束校验、前缀稳定、边界情形。

运行：
    python3 test_detdata.py            # 快速套件（默认）
    python3 test_detdata.py --large    # 追加超大规模用例（200k 用户 / 1M 订单）
"""

import subprocess
import sys
import time
import unittest

import detdata
from detdata import (
    ConflictingConstraintsError,
    Entity,
    Field,
    Schema,
    default_schema,
    digest,
    generate,
)
from validator import format_report, validate

SEED = "selftest-seed"
RUN_LARGE = False
# 空集合（users=0, orders=0, seed="pin"）的钉死摘要：防止序列化格式意外漂移。
EMPTY_DIGEST = "2ff34d9d167c7459d5c2ca477d41450ad7b08ec16806dfd17ac3b337036da462"


class TestDeterminism(unittest.TestCase):
    def test_same_seed_twice_byte_identical(self):
        counts = {"users": 500, "orders": 2000}
        a = generate(default_schema(), SEED, counts)
        b = generate(default_schema(), SEED, counts)
        self.assertEqual(detdata.canonical_bytes(a), detdata.canonical_bytes(b))
        self.assertEqual(digest(a), digest(b))

    def test_digest_stable_across_processes(self):
        """子进程独立生成，摘要必须与当前进程一致（排除进程内状态影响）。"""
        counts = {"users": 100, "orders": 300}
        here = digest(generate(default_schema(), SEED, counts))
        out = subprocess.run(
            [sys.executable, "detdata.py", "digest",
             "--seed", SEED, "--users", "100", "--orders", "300"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(out.stdout.strip(), here)

    def test_different_seed_different_data(self):
        counts = {"users": 100, "orders": 300}
        a = generate(default_schema(), "seed-A", counts)
        b = generate(default_schema(), "seed-B", counts)
        self.assertNotEqual(digest(a), digest(b))
        # 但各自仍满足全部约束
        self.assertTrue(validate(a, default_schema())["ok"])
        self.assertTrue(validate(b, default_schema())["ok"])


class TestConstraints(unittest.TestCase):
    def test_declared_constraints_hold(self):
        data = generate(default_schema(), SEED, {"users": 1000, "orders": 5000})
        report = validate(data, default_schema())
        if not report["ok"]:  # 失败时打印可读报告便于定位
            self.fail("\n" + format_report(report))

    def test_validator_detects_tampering(self):
        data = generate(default_schema(), SEED, {"users": 50, "orders": 100})
        data["users"][0]["age"] = 5            # 违反 min=18
        data["users"][1]["id"] = data["users"][0]["id"]  # 违反唯一性
        data["orders"][0]["user_id"] = 2**31   # 违反引用完整性
        report = validate(data, default_schema())
        self.assertFalse(report["ok"])
        self.assertIn("age", report["entities"]["users"]["fields"])
        self.assertIn("id", report["entities"]["users"]["fields"])
        self.assertIn("user_id", report["entities"]["orders"]["fields"])


class TestPrefixStability(unittest.TestCase):
    def test_users_prefix_stable_when_users_grow(self):
        """users 自身规模扩大（orders 固定）：已有 users 行逐行一致。"""
        small = generate(default_schema(), SEED, {"users": 100, "orders": 300})
        grown = generate(default_schema(), SEED, {"users": 1000, "orders": 300})
        self.assertEqual(small["users"], grown["users"][:100])

    def test_orders_prefix_stable_when_orders_grow(self):
        """orders 自身规模扩大（users 固定）：已有 orders 行逐行一致。"""
        small = generate(default_schema(), SEED, {"users": 100, "orders": 300})
        grown = generate(default_schema(), SEED, {"users": 100, "orders": 5000})
        self.assertEqual(small["orders"], grown["orders"][:300])

    def test_users_bytes_prefix_stable(self):
        """字节级：小规模 users 的规范化字节 == 大规模前 100 行的规范化字节。"""
        small = generate(default_schema(), SEED, {"users": 100, "orders": 0})
        grown = generate(default_schema(), SEED, {"users": 1000, "orders": 0})
        small_bytes = detdata.canonical_bytes({"users": small["users"]})
        grown_prefix_bytes = detdata.canonical_bytes({"users": grown["users"][:100]})
        self.assertEqual(small_bytes, grown_prefix_bytes)


class TestEdgeCases(unittest.TestCase):
    def test_empty_collections(self):
        data = generate(default_schema(), SEED, {"users": 0, "orders": 0})
        self.assertEqual(data, {"users": [], "orders": []})
        self.assertTrue(validate(data, default_schema())["ok"])

    def test_empty_digest_pinned(self):
        data = generate(default_schema(), "pin", {"users": 0, "orders": 0})
        self.assertEqual(digest(data), EMPTY_DIGEST)

    def test_conflicting_unique_int_range(self):
        schema = Schema((Entity("t", (Field("x", "int", min=1, max=3, unique=True),)),))
        with self.assertRaises(ConflictingConstraintsError):
            generate(schema, SEED, {"t": 4})

    def test_conflicting_unique_choices(self):
        schema = Schema((Entity("t", (Field("c", "choice", choices=("a", "b"), unique=True),)),))
        with self.assertRaises(ConflictingConstraintsError):
            generate(schema, SEED, {"t": 3})

    def test_conflicting_empty_range(self):
        schema = Schema((Entity("t", (Field("x", "int", min=10, max=5),)),))
        with self.assertRaises(ConflictingConstraintsError):
            generate(schema, SEED, {"t": 1})

    def test_conflicting_missing_ref_entity(self):
        schema = Schema((Entity("t", (Field("r", "ref", ref_entity="ghost", ref_field="id"),)),))
        with self.assertRaises(ConflictingConstraintsError):
            generate(schema, SEED, {"t": 1})

    def test_conflicting_ref_to_empty_entity(self):
        with self.assertRaises(ConflictingConstraintsError):
            generate(default_schema(), SEED, {"users": 0, "orders": 1})


class TestLargeScale(unittest.TestCase):
    """超大规模：默认跳过，用 `python3 test_detdata.py --large` 启用。"""
    def test_large_scale(self):
        if not RUN_LARGE:
            self.skipTest("用 --large 启用超大规模用例")
        t0 = time.perf_counter()
        mid_orders = generate(default_schema(), SEED, {"users": 200_000, "orders": 5000})
        small_users = generate(default_schema(), SEED, {"users": 1000, "orders": 5000})
        data = generate(default_schema(), SEED, {"users": 200_000, "orders": 1_000_000})
        gen_s = time.perf_counter() - t0
        report = validate(data, default_schema())
        self.assertTrue(report["ok"], "\n" + format_report(report))
        # 规模变化后前缀仍然稳定：users 随 users 规模、orders 随 orders 规模
        self.assertEqual(small_users["users"], data["users"][:1000])
        self.assertEqual(mid_orders["orders"], data["orders"][:5000])
        print(f"\n[large] 生成 200k 用户 + 1M 订单耗时 {gen_s:.1f}s，约束全部通过，前缀稳定")


if __name__ == "__main__":
    if "--large" in sys.argv:
        sys.argv.remove("--large")
        RUN_LARGE = True
    unittest.main(verbosity=2)
