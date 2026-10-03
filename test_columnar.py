"""列存转换库自测：边界用例 + 还原对拍 + 查询对拍。

运行：python3 -m unittest test_columnar -v
"""

import random
import unittest

from columnar import (
    ColumnarTable,
    decode_column,
    encode_column,
    normalize_rows,
    query_columnar,
    query_rows,
)


def make_rows(specs, n):
    """specs: {列名: 生成函数(i, rng) -> 值}"""
    rng = random.Random(20261004)
    return [{name: gen(i, rng) for name, gen in specs.items()} for i in range(n)]


class TestColumnRoundTrip(unittest.TestCase):
    """列级编码 -> 解码必须逐行还原。"""

    def roundtrip(self, values):
        col = encode_column("c", values)
        self.assertEqual(decode_column(col), values)
        return col

    def test_all_null(self):
        col = self.roundtrip([None] * 1000)
        self.assertEqual(col["null_count"], 1000)
        self.assertEqual(col["payload"], [])

    def test_single_value(self):
        col = self.roundtrip([42] * 5000)
        self.assertEqual(col["encoding"], "rle")
        self.assertEqual(len(col["payload"]), 1)

    def test_highly_repetitive(self):
        values = []
        for v in (7, 7, 7, 9, 9, None, None, 7):
            values.extend([v] * 250)
        col = self.roundtrip(values)
        self.assertEqual(col["encoding"], "rle")

    def test_mixed_types(self):
        # 1 / True / 1.0 / "1" 在 Python 里互相 ==，必须靠类型标签区分
        values = [1, True, 1.0, "1", None, 0, False, 0.0, "", "0"] * 100
        self.roundtrip(values)

    def test_null_not_default(self):
        # None 不能被还原成 0 / "" / False
        values = [None, 0, None, "", None, False, None]
        col = encode_column("c", values)
        self.assertEqual(decode_column(col), values)

    def test_empty_column(self):
        col = self.roundtrip([])
        self.assertEqual(col["rows"], 0)

    def test_single_row(self):
        self.roundtrip([None])
        self.roundtrip(["x"])

    def test_special_floats(self):
        values = [float("nan"), float("inf"), -float("inf"), 0.1, -0.0]
        col = encode_column("c", values)
        got = decode_column(col)
        self.assertTrue(got[0] != got[0])          # NaN
        self.assertEqual(got[1:], values[1:])

    def test_dict_encoding_chosen(self):
        rng = random.Random(1)
        values = [rng.choice(["a", "b", "c"]) for _ in range(2000)]
        col = self.roundtrip(values)
        self.assertEqual(col["encoding"], "dict")

    def test_fuzz_roundtrip(self):
        rng = random.Random(7)
        pool = [None, 0, 1, -5, 3.14, True, False, "", "hello", "世界", 10**12]
        for _ in range(50):
            values = [rng.choice(pool) for _ in range(rng.randint(0, 300))]
            self.roundtrip(values)


class TestTableRoundTrip(unittest.TestCase):
    """表级：行存 -> 列存 -> 行存，逐行对拍。"""

    def check(self, rows):
        table = ColumnarTable.from_rows(rows)
        expected, _ = normalize_rows(rows)
        self.assertEqual(table.to_rows(), expected)
        # JSON 序列化后再还原也必须一致
        table2 = ColumnarTable.from_json(table.to_json())
        self.assertEqual(table2.to_rows(), expected)

    def test_empty_table(self):
        self.check([])

    def test_all_null_column(self):
        self.check([{"id": i, "note": None} for i in range(500)])

    def test_single_value_column(self):
        self.check([{"id": i, "flag": True} for i in range(500)])

    def test_highly_repetitive_column(self):
        rows = [{"id": i, "status": "ok" if i % 50 else "err"} for i in range(2000)]
        self.check(rows)

    def test_mixed_type_column(self):
        pool = [1, True, 1.5, "x", None, False]
        rng = random.Random(3)
        self.check([{"v": rng.choice(pool)} for _ in range(1000)])

    def test_missing_keys_become_null(self):
        rows = [{"a": 1}, {"b": 2}, {"a": 3, "b": None}]
        self.check(rows)


class TestQueryDiff(unittest.TestCase):
    """查询对拍：同一查询在行存与列存上的结果必须逐行一致。"""

    @classmethod
    def setUpClass(cls):
        rng = random.Random(99)
        cls.rows = make_rows({
            "id": lambda i, r: i,
            "grp": lambda i, r: r.choice(["a", "b", "c", None]),
            "score": lambda i, r: r.choice([None, 0, 50, 100, r.randint(0, 100)]),
            "flag": lambda i, r: r.choice([True, False, None]),
        }, 3000)
        cls.table = ColumnarTable.from_rows(cls.rows)

    def diff(self, select, filters):
        got_row = query_rows(self.rows, select, filters)
        got_col = query_columnar(self.table, select, filters)
        self.assertEqual(got_row, got_col)
        return len(got_row)

    def test_full_scan(self):
        self.assertEqual(self.diff(["id", "grp", "score", "flag"], []), 3000)

    def test_equality_and_null(self):
        self.diff(["id"], [("grp", "==", "a")])
        self.diff(["id"], [("grp", "is_null", None)])
        self.diff(["id"], [("score", "not_null", None)])
        self.diff(["id", "score"], [("score", "==", 100)])

    def test_ranges(self):
        self.diff(["id"], [("score", ">=", 50), ("score", "<", 100)])
        self.diff(["id"], [("id", ">", 2500)])

    def test_bool_not_confused_with_int(self):
        # flag=True 不应匹配 score==1 之类的语义；True 只匹配 True
        self.diff(["id"], [("flag", "==", True)])
        self.diff(["id"], [("flag", "==", 1)])

    def test_empty_result(self):
        self.assertEqual(self.diff(["id"], [("id", "==", 10**9)]), 0)

    def test_random_queries(self):
        rng = random.Random(5)
        cols = ["id", "grp", "score", "flag"]
        ops = ["==", "!=", "<", "<=", ">", ">=", "is_null", "not_null"]
        args = {"id": 1500, "grp": "b", "score": 60, "flag": False}
        for _ in range(200):
            select = rng.sample(cols, rng.randint(1, 4))
            filters = []
            for _ in range(rng.randint(0, 3)):
                col = rng.choice(cols)
                op = rng.choice(ops)
                filters.append((col, op, None if "null" in op else args[col]))
            self.diff(select, filters)


if __name__ == "__main__":
    unittest.main()
