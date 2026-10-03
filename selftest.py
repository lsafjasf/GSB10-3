"""自测：边界用例 + 行存/列存对拍（仅标准库 unittest）。

运行：python3 selftest.py [-v]
"""

import random
import unittest

import columnar


def rows_equal(a, b):
    """逐行逐值比较，float 用 == 即可（编码走 struct 8 字节，精度无损）。"""
    return list(a) == list(b)


class RoundTripTest(unittest.TestCase):
    """编码前后逐行还原：覆盖题目要求的边界情形。"""

    def roundtrip(self, columns, rows):
        blob = columnar.encode_table(columns, rows)
        cols2, rows2 = columnar.decode_table(blob)
        self.assertEqual(list(columns), cols2)
        self.assertTrue(rows_equal([tuple(r) for r in rows], rows2))
        return blob

    def test_all_null_column(self):
        # 全空列：所有值都是 None
        cols = ["id", "note"]
        rows = [(i, None) for i in range(1000)]
        blob = self.roundtrip(cols, rows)
        self.assertEqual(columnar.decode_column_from_blob(blob, "note"),
                         [None] * 1000)

    def test_single_value_column(self):
        # 单值列：整列同一个值
        cols = ["id", "flag"]
        rows = [(i, "CONST") for i in range(5000)]
        blob = self.roundtrip(cols, rows)
        h, _ = columnar._read_header(blob)
        enc = {c["name"]: c["encoding"] for c in h["columns"]}
        self.assertEqual(enc["flag"], "rle")  # 单值列应命中游程编码

    def test_highly_repetitive_column(self):
        # 高度重复列：少量 distinct 值乱序出现 -> 字典编码
        rng = random.Random(7)
        vals = ["北京", "上海", "广州", "深圳"]
        cols = ["city"]
        rows = [(rng.choice(vals),) for _ in range(20000)]
        blob = self.roundtrip(cols, rows)
        h, _ = columnar._read_header(blob)
        self.assertEqual(h["columns"][0]["encoding"], "dict")

    def test_mixed_type_column(self):
        # 混合类型列：int / float / str / bool / None 混在同一列
        vals = [1, 2.5, "三", True, False, None, -999, 0.0, "", 3.14]
        rows = [(vals[i % len(vals)],) for i in range(1000)]
        self.roundtrip(["mixed"], rows)

    def test_null_not_default(self):
        # 空值不能被还原成 0 / "" / False 等默认值
        cols = ["v"]
        rows = [(None,), (0,), ("",), (False,), (None,), (0.0,)]
        blob = self.roundtrip(cols, rows)
        got = columnar.decode_column_from_blob(blob, "v")
        self.assertEqual(got, [None, 0, "", False, None, 0.0])
        self.assertIs(got[0], None)
        self.assertIs(got[4], None)

    def test_empty_table(self):
        self.roundtrip(["a", "b"], [])

    def test_single_row(self):
        self.roundtrip(["a"], [(42,)])

    def test_all_null_every_column(self):
        cols = ["a", "b", "c"]
        rows = [(None, None, None)] * 500
        self.roundtrip(cols, rows)

    def test_unicode_and_long_strings(self):
        rows = [("汉字🙂" * i,) for i in range(50)]
        self.roundtrip(["s"], rows)

    def test_large_ints_and_floats(self):
        rows = [(2**62, 1e308), (-2**62, -1e-308), (0, 0.0)]
        self.roundtrip(["i", "f"], rows)

    def test_fuzz_roundtrip(self):
        rng = random.Random(2026)
        pool = [None, 0, 1, -1, 255, 2**40, 3.14, -0.5, "", "a", "汉字",
                True, False, "x" * 100]
        for trial in range(200):
            ncol = rng.randint(1, 6)
            nrow = rng.randint(0, 300)
            cols = ["c%d" % i for i in range(ncol)]
            rows = [tuple(rng.choice(pool) for _ in range(ncol))
                    for _ in range(nrow)]
            cols2, rows2 = columnar.decode_table(columnar.encode_table(cols, rows))
            self.assertEqual(cols, cols2, "trial %d" % trial)
            self.assertTrue(rows_equal([tuple(r) for r in rows], rows2),
                            "trial %d" % trial)


class DiffTest(unittest.TestCase):
    """对拍：同一批数据、同一谓词，行存查询 vs 列存查询必须逐行一致。"""

    def _gen_table(self, rng, nrow):
        cities = ["北京", "上海", "广州", None]
        rows = []
        for _ in range(nrow):
            rows.append((
                rng.randint(0, 100),                    # age
                rng.choice(cities),                     # city（含空值）
                rng.choice([0.0, 9.9, 19.9, None]),     # price（含空值）
                rng.choice([True, False]),              # vip
            ))
        return ["age", "city", "price", "vip"], rows

    def test_diff_row_vs_columnar(self):
        rng = random.Random(42)
        total_cases = 0
        mismatches = 0
        samples = []
        for trial in range(50):
            cols, rows = self._gen_table(rng, rng.randint(0, 500))
            blob = columnar.encode_table(cols, rows)
            # 随机谓词组合：等值 / 范围 / 空值判断；阈值预绑定，保证两侧同一谓词
            age_hi = rng.randint(0, 100)
            city_target = rng.choice(["北京", "上海", None])
            preds = [
                (lambda r, t=age_hi: r["age"] > t, ("age",)),
                (lambda r, t=city_target: r["city"] == t, ("city",)),
                (lambda r: r["price"] is None, ("price",)),
                (lambda r: r["vip"] and r["age"] < 50, ("vip", "age")),
                (None, ()),
            ]
            for where, where_cols in preds:
                for select in [("age",), ("city", "age"), cols]:
                    got_row = columnar.row_query(cols, rows, select, where)
                    got_col = columnar.col_query(blob, select, where, where_cols)
                    total_cases += 1
                    if not rows_equal(got_row, got_col):
                        mismatches += 1
                    elif len(samples) < 3 and got_row:
                        samples.append((select, got_row[:3]))
        self.assertEqual(0, mismatches)
        print("\n[对拍] 用例数=%d 不一致=%d 示例=%s"
              % (total_cases, mismatches, samples[:1]))

    def test_lazy_column_decode_consistent(self):
        # 按列懒解码结果 == 整表解码对应列
        cols = ["a", "b", "c"]
        rng = random.Random(1)
        rows = [(rng.randint(0, 9), rng.choice([None, "v"]), rng.random())
                for _ in range(1000)]
        blob = columnar.encode_table(cols, rows)
        _, all_rows = columnar.decode_table(blob)
        for i, name in enumerate(cols):
            lazy = columnar.decode_column_from_blob(blob, name)
            self.assertEqual(lazy, [r[i] for r in all_rows])


if __name__ == "__main__":
    unittest.main(verbosity=2)
