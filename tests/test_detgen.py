"""detgen 自测：摘要对比、约束校验、前缀稳定、边界用例。

运行方式（仓库根目录）：
    python3 tests/test_detgen.py     # 打印演示报告 + 跑全部用例
    python3 -m unittest discover -s tests -v
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detgen import (
    ConstraintConflictError,
    Field,
    Schema,
    SchemaError,
    Table,
    generate,
    validate_dataset,
)

SEED = "gsb10-3-fixed-seed"

# 冻结的黄金摘要：同一版本代码两次生成必须逐字节一致。
# 若有意变更生成算法，需重新生成并审查这些值（见文件底部说明）。
GOLDEN_DIGESTS = {
    "demo": "9d1c87c4278754c92147ebe39ef1c622d2eb61e4ff6948cb5c804b60e3ab9785",
    "empty": "2347ea0ba1e92873ab9b8e50e42702fd96f75e48dfbc817eca92850f308ff1a1",
    "prefix_100": "6b354a790494e7d1c7bfa7c268566dc30307db7c2c92bee8256e2939db8df94d",
    "seed_a": "9ae4ed2dec267aaea3e87c0dc66e7e5fc5b3d1a226d6ef24d6b1c542f72e7ea5",
    "seed_b": "eed346dc76378533ab02e95315a111aabaaf0b177cfb90d83064541c02d6bf35",
}


def demo_schema():
    return Schema.build(
        [
            Table(
                "user",
                (
                    Field.integer("id", 1, 10**9, unique=True),
                    Field.text("name", 8),
                    Field.integer("age", 18, 80),
                    Field.choice("role", ("admin", "dev", "ops")),
                    Field.boolean("active"),
                ),
            ),
            Table(
                "order",
                (
                    Field.text("order_no", 10, unique=True),
                    Field.reference("user_id", "user", "id"),
                    Field.integer("amount", 1, 100000),
                ),
            ),
        ]
    )


def demo_counts():
    return {"user": 50, "order": 200}


def demo_dataset(seed=SEED):
    return generate(demo_schema(), demo_counts(), seed=seed)


class TestDeterminism(unittest.TestCase):
    """固定种子两次生成逐字节一致，并与冻结的黄金摘要一致。"""

    def test_same_seed_twice_byte_identical(self):
        first = demo_dataset()
        second = demo_dataset()
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.digest(), second.digest())

    def test_golden_digest_unchanged(self):
        self.assertEqual(demo_dataset().digest(), GOLDEN_DIGESTS["demo"])

    def test_digest_stable_across_processes(self):
        code = (
            "import sys; sys.path.insert(0, '.');"
            "from tests.test_detgen import demo_dataset;"
            "print(demo_dataset().digest())"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), demo_dataset().digest())


class TestConstraints(unittest.TestCase):
    """生成结果满足声明式约束：范围、唯一性、引用完整性。"""

    def test_generated_data_passes_validation(self):
        report = validate_dataset(demo_schema(), demo_dataset())
        for line in report.summary_lines():
            print("  " + line)
        self.assertTrue(report.ok)
        self.assertEqual(report.violation_count, 0)

    def test_validator_catches_tampering(self):
        dataset = demo_dataset()
        tampered = [dict(row) for row in dataset.tables["user"]]
        tampered[0]["age"] = 5  # 越界
        tampered[1]["id"] = tampered[0]["id"]  # 破坏唯一性
        from detgen import Dataset

        broken = Dataset(
            tables={**dataset.tables, "user": tampered}, seed=dataset.seed
        )
        report = validate_dataset(demo_schema(), broken)
        self.assertFalse(report.ok)
        self.assertGreaterEqual(report.violation_count, 2)


class TestPrefixStability(unittest.TestCase):
    """规模变化时已有数据的顺序和内容保持稳定。"""

    def test_same_table_growth_keeps_prefix(self):
        # 父表规模固定时，order 从 40 增到 400，前 40 行逐字节不变；
        # user 从 10 增到 100 时，前 10 行同样不变。
        small = generate(demo_schema(), {"user": 100, "order": 40}, seed=SEED)
        large = generate(demo_schema(), {"user": 100, "order": 400}, seed=SEED)
        self.assertEqual(small.tables["order"], large.tables["order"][:40])
        self.assertEqual(small.tables["user"], large.tables["user"])

        small_users = generate(demo_schema(), {"user": 10, "order": 0}, seed=SEED)
        large_users = generate(demo_schema(), {"user": 100, "order": 0}, seed=SEED)
        self.assertEqual(small_users.tables["user"], large_users.tables["user"][:10])

    def test_reference_stability_semantics(self):
        # 外键行是 (种子, 行号, 父表已生成数据) 的纯函数：
        # 父表相同时子行稳定；父表候选集增长时引用允许变化
        # （不可能在集合增长的同时又保持选择逐字节不变，二者数学上冲突）。
        a = generate(demo_schema(), {"user": 100, "order": 50}, seed=SEED)
        b = generate(demo_schema(), {"user": 100, "order": 999}, seed=SEED)
        self.assertEqual(a.tables["order"], b.tables["order"][:50])
        grown = generate(demo_schema(), {"user": 500, "order": 50}, seed=SEED)
        self.assertNotEqual(a.tables["order"], grown.tables["order"])
        report = validate_dataset(demo_schema(), grown)
        self.assertTrue(report.ok)

    def test_prefix_digest_matches_frozen_value(self):
        large = generate(demo_schema(), {"user": 100, "order": 400}, seed=SEED)
        prefix = large.sliced({"user": 100, "order": 100})
        self.assertEqual(prefix.digest(), GOLDEN_DIGESTS["prefix_100"])
        exact = generate(demo_schema(), {"user": 100, "order": 100}, seed=SEED)
        self.assertEqual(prefix.canonical_bytes(), exact.canonical_bytes())


class TestEdgeCases(unittest.TestCase):
    """空集合、超大规模、约束冲突、种子不同。"""

    def test_empty_dataset(self):
        dataset = generate(demo_schema(), {"user": 0, "order": 0}, seed=SEED)
        self.assertEqual(dataset.tables, {"user": [], "order": []})
        self.assertEqual(dataset.digest(), GOLDEN_DIGESTS["empty"])
        report = validate_dataset(demo_schema(), dataset)
        self.assertTrue(report.ok)

    def test_missing_table_defaults_to_empty(self):
        dataset = generate(demo_schema(), {}, seed=SEED)
        self.assertEqual(dataset.tables, {"user": [], "order": []})

    def test_large_scale(self):
        counts = {"user": 100_000, "order": 100_000}
        first = generate(demo_schema(), counts, seed=SEED)
        second = generate(demo_schema(), counts, seed=SEED)
        self.assertEqual(first.digest(), second.digest())
        report = validate_dataset(demo_schema(), first)
        self.assertTrue(report.ok, msg="\n".join(report.summary_lines()))
        # 10 万行下唯一性确实成立
        ids = [row["id"] for row in first.tables["user"]]
        self.assertEqual(len(set(ids)), 100_000)

    def test_conflicting_unique_integer(self):
        schema = Schema.build(
            [Table("t", (Field.integer("id", 1, 3, unique=True),))]
        )
        with self.assertRaises(ConstraintConflictError):
            generate(schema, {"t": 4}, seed=SEED)

    def test_conflicting_unique_choice(self):
        schema = Schema.build(
            [Table("t", (Field.choice("c", ("x", "y"), unique=True),))]
        )
        with self.assertRaises(ConstraintConflictError):
            generate(schema, {"t": 3}, seed=SEED)

    def test_conflicting_unique_text(self):
        schema = Schema.build([Table("t", (Field.text("s", 1, unique=True),))])
        # 36^1 = 36 个空间，37 行无解
        with self.assertRaises(ConstraintConflictError):
            generate(schema, {"t": 37}, seed=SEED)
        # 36 行恰好有解
        dataset = generate(schema, {"t": 36}, seed=SEED)
        self.assertTrue(validate_dataset(schema, dataset).ok)

    def test_invalid_schema_rejected(self):
        with self.assertRaises(SchemaError):
            Schema.build([Table("t", (Field.integer("bad", 10, 1),))])
        with self.assertRaises(SchemaError):
            Schema.build(
                [Table("t", (Field.reference("r", "ghost", "id"),))]
            )

    def test_different_seeds_differ_but_each_deterministic(self):
        data_a = demo_dataset(seed="seed-A")
        data_b = demo_dataset(seed="seed-B")
        self.assertNotEqual(data_a.digest(), data_b.digest())
        self.assertEqual(data_a.digest(), GOLDEN_DIGESTS["seed_a"])
        self.assertEqual(data_b.digest(), GOLDEN_DIGESTS["seed_b"])
        self.assertEqual(data_a.digest(), demo_dataset(seed="seed-A").digest())
        self.assertEqual(data_b.digest(), demo_dataset(seed="seed-B").digest())


def print_demo():
    print("=" * 60)
    print("演示：固定种子生成 + 摘要 + 约束校验")
    print("=" * 60)
    dataset = demo_dataset()
    print(f"种子: {SEED!r}  规模: {demo_counts()}")
    print(f"SHA-256 摘要: {dataset.digest()}")
    print("user 表前 3 行:")
    for row in dataset.tables["user"][:3]:
        print(f"  {row}")
    print("约束校验结果:")
    report = validate_dataset(demo_schema(), dataset)
    for line in report.summary_lines():
        print("  " + line)
    print()


if __name__ == "__main__":
    print_demo()
    unittest.main(argv=[sys.argv[0], "-v"], exit=True)
