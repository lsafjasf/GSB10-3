"""dictz 回归测试：往返、字典持久化、旧格式/旧字典兼容、边界用例。

运行：python3 -m unittest test_dictz -v
"""

import json
import os
import random
import shutil
import tempfile
import unittest

import dictz


def make_messages(seed, n):
    """生成结构相似的短消息（模拟日志/事件上报）。"""
    rng = random.Random(seed)
    events = ["login", "logout", "purchase", "click", "signup", "page_view"]
    msgs = []
    for _ in range(n):
        msg = {
            "ts": 1700000000 + rng.randint(0, 99999),
            "level": rng.choice(["INFO", "DEBUG", "WARN", "ERROR"]),
            "event": rng.choice(events),
            "user_id": rng.randint(1000, 9999),
            "session": "%08x" % rng.getrandbits(32),
            "message": rng.choice([
                "user action completed",
                "request processed successfully",
                "cache miss, fallback to db",
            ]),
        }
        msgs.append(json.dumps(msg, separators=(",", ":")).encode())
    return msgs


class RoundTripTest(unittest.TestCase):
    def test_roundtrip_plain(self):
        samples = make_messages(1, 50) + [b"", b"a", b"ab", b"abc",
                                          bytes(range(256)), b"x" * 10000]
        for data in samples:
            self.assertEqual(dictz.decompress(dictz.compress(data)), data)

    def test_roundtrip_random(self):
        rng = random.Random(42)
        for _ in range(20):
            data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 2000)))
            self.assertEqual(dictz.decompress(dictz.compress(data)), data)

    def test_roundtrip_with_dict(self):
        train = make_messages(2, 100)
        dictionary = dictz.train_dictionary(train, dict_size=1024)
        for data in make_messages(3, 50) + [b"", b"short"]:
            blob = dictz.compress(data, dictionary)
            self.assertEqual(dictz.decompress(blob, dictionary), data)

    def test_empty_input(self):
        self.assertEqual(dictz.decompress(dictz.compress(b"")), b"")
        dictionary = dictz.train_dictionary(make_messages(4, 20), 512)
        blob = dictz.compress(b"", dictionary)
        self.assertEqual(dictz.decompress(blob, dictionary), b"")

    def test_no_builtin_compression_modules(self):
        self.assertNotIn("zlib", vars(dictz))
        self.assertNotIn("lzma", vars(dictz))
        self.assertNotIn("bz2", vars(dictz))


class DictionaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_persistence_roundtrip(self):
        dictionary = dictz.train_dictionary(make_messages(5, 100), 1024)
        path = os.path.join(self.tmp, "d.dzd")
        did = dictz.save_dictionary(dictionary, path)
        loaded = dictz.load_dictionary(path)
        self.assertEqual(loaded, dictionary)
        self.assertEqual(did, dictz.dict_id(dictionary))
        # 用加载后的字典压缩/解压必须兼容
        data = make_messages(6, 1)[0]
        blob = dictz.compress(data, loaded)
        self.assertEqual(dictz.decompress(blob, dictionary), data)

    def test_corrupt_dict_file_rejected(self):
        path = os.path.join(self.tmp, "bad.dzd")
        dictz.save_dictionary(b"hello dictionary", path)
        with open(path, "r+b") as f:
            f.seek(20)
            f.write(b"X")
        with self.assertRaises(dictz.DictzError):
            dictz.load_dictionary(path)

    def test_dict_improves_ratio(self):
        train = make_messages(7, 200)
        test = make_messages(8, 200)
        dictionary = dictz.train_dictionary(train, 1024)
        plain = sum(len(dictz.compress(m)) for m in test)
        with_dict = sum(len(dictz.compress(m, dictionary)) for m in test)
        self.assertLess(with_dict, plain)

    def test_mismatched_dict_raises(self):
        dict_a = dictz.train_dictionary(make_messages(9, 100), 1024)
        dict_b = dictz.train_dictionary(make_messages(10, 100), 1024)
        self.assertNotEqual(dictz.dict_id(dict_a), dictz.dict_id(dict_b))
        blob = dictz.compress(make_messages(11, 1)[0], dict_a)
        with self.assertRaises(dictz.DictMismatchError):
            dictz.decompress(blob, dict_b)
        # 完全不提供字典也必须报错，并指明需要的 dict_id
        with self.assertRaises(dictz.DictMismatchError) as ctx:
            dictz.decompress(blob)
        self.assertEqual(ctx.exception.needed, dictz.dict_id(dict_a).hex())

    def test_old_dictionary_compat_via_store(self):
        """字典升级后，旧数据用旧字典仍可读取。"""
        store = dictz.DictStore(os.path.join(self.tmp, "store"))
        # 第一代字典
        dict_v1 = dictz.train_dictionary(make_messages(12, 50), 1024)
        store.save(dict_v1)
        old_data = make_messages(13, 10)
        old_blobs = [dictz.compress(m, dict_v1) for m in old_data]
        # 样本扩充后重新训练 -> 新 dict_id
        dict_v2 = dictz.train_dictionary(make_messages(12, 500), 1024)
        self.assertNotEqual(dictz.dict_id(dict_v1), dictz.dict_id(dict_v2))
        store.save(dict_v2)
        new_data = make_messages(14, 10)
        new_blobs = [dictz.compress(m, dict_v2) for m in new_data]
        # store 按帧内 dict_id 自动选字典：新旧数据都能解开
        for blob, expect in zip(old_blobs, old_data):
            self.assertEqual(store.decompress(blob), expect)
        for blob, expect in zip(new_blobs, new_data):
            self.assertEqual(store.decompress(blob), expect)

    def test_legacy_v1_frame_compat(self):
        """旧版本（v1，无字典头）产出的数据仍可解压。"""
        data = make_messages(15, 5)
        for m in data:
            blob = dictz.compress(m, _format_version=dictz.FORMAT_VERSION_LEGACY)
            self.assertEqual(blob[2], 1)
            self.assertEqual(dictz.decompress(blob), m)
            # 旧数据不带 dict_id，DictStore 走无字典路径也能解
            store = dictz.DictStore(os.path.join(self.tmp, "s2"))
            self.assertEqual(store.decompress(blob), m)


class EdgeCaseTest(unittest.TestCase):
    def test_few_samples(self):
        # 0 个样本 -> 空字典，压缩照常工作
        self.assertEqual(dictz.train_dictionary([], 1024), b"")
        # 1 个样本 -> 退化为样本内容，不报错
        d1 = dictz.train_dictionary([b'{"event":"login"}'], 1024)
        self.assertTrue(d1)
        data = b'{"event":"login","user":42}'
        self.assertEqual(dictz.decompress(dictz.compress(data, d1), d1), data)
        # 2 个极短样本（短于 k=8）
        d2 = dictz.train_dictionary([b"hi", b"yo"], 1024)
        self.assertTrue(d2)

    def test_highly_repetitive_samples(self):
        train = [b'{"event":"login","status":"ok"}'] * 200
        dictionary = dictz.train_dictionary(train, 512)
        data = b'{"event":"login","status":"ok"}'
        plain = dictz.compress(data)
        with_dict = dictz.compress(data, dictionary)
        self.assertLess(len(with_dict), len(plain))
        self.assertEqual(dictz.decompress(with_dict, dictionary), data)

    def test_dict_data_mismatch(self):
        """字典与数据完全不相关：不报错、可往返，体积不退化超过帧头开销。"""
        dictionary = dictz.train_dictionary(make_messages(16, 100), 1024)
        data = bytes(range(256)) * 4  # 与字典毫无关系
        blob = dictz.compress(data, dictionary)
        self.assertEqual(dictz.decompress(blob, dictionary), data)
        overhead = len(blob) - len(dictz.compress(data))
        self.assertLessEqual(overhead, dictz.DICT_ID_LEN)

    def test_empty_and_tiny_dict_size(self):
        self.assertEqual(dictz.train_dictionary(make_messages(17, 10), 0), b"")
        d = dictz.train_dictionary(make_messages(17, 10), 8)
        self.assertLessEqual(len(d), 8)
        data = make_messages(18, 1)[0]
        self.assertEqual(dictz.decompress(dictz.compress(data, d), d), data)

    def test_large_repetitive_payload(self):
        data = b"abc123" * 5000
        blob = dictz.compress(data)
        self.assertLess(len(blob), len(data) // 10)
        self.assertEqual(dictz.decompress(blob), data)


if __name__ == "__main__":
    unittest.main()

