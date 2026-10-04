"""lzdict 回归测试：往返、边界、字典版本兼容、字典不匹配等。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lzdict import (
    compress,
    decompress,
    train_dictionary,
    dict_id_for,
    DictStore,
    DictionaryNotFoundError,
    CorruptDataError,
    IntegrityError,
)

SAMPLES = [
    '{"ts":"2026-10-04T10:00:01Z","level":"INFO","service":"auth","event":"login_success","user_id":482131,"latency_ms":42}',
    '{"ts":"2026-10-04T10:00:02Z","level":"ERROR","service":"billing","event":"payment_failed","user_id":120042,"latency_ms":781}',
    '{"ts":"2026-10-04T10:00:03Z","level":"WARN","service":"gateway","event":"rate_limit","user_id":771553,"latency_ms":15}',
    '{"ts":"2026-10-04T10:00:04Z","level":"INFO","service":"auth","event":"login_success","user_id":330918,"latency_ms":55}',
    '{"ts":"2026-10-04T10:00:05Z","level":"ERROR","service":"search","event":"query_timeout","user_id":221047,"latency_ms":900}',
    '{"ts":"2026-10-04T10:00:06Z","level":"INFO","service":"billing","event":"payment_created","user_id":908122,"latency_ms":130}',
    '{"ts":"2026-10-04T10:00:07Z","level":"WARN","service":"auth","event":"login_failed","user_id":115674,"latency_ms":88}',
    '{"ts":"2026-10-04T10:00:08Z","level":"INFO","service":"gateway","event":"request_done","user_id":663201,"latency_ms":7}',
]


class RoundTripTest(unittest.TestCase):
    def roundtrip(self, data, dictionary=b"", dict_id=""):
        if dictionary:
            dict_id = dict_id or dict_id_for(dictionary)
        blob = compress(data, dictionary, dict_id)
        loader = (lambda wanted: dictionary if wanted == dict_id else None) if dict_id else None
        result = decompress(blob, loader)
        self.assertEqual(result, data)
        return blob

    def test_plain_text_no_dict(self):
        self.roundtrip("hello world, hello world, hello world".encode())

    def test_binary_no_dict(self):
        for n in (0, 1, 2, 3, 4, 5, 127, 128, 129, 1000, 5000):
            self.roundtrip(os.urandom(n))

    def test_repetitive_rle_style(self):
        self.roundtrip(b"A" * 10000)
        self.roundtrip(b"ABCD" * 2000)

    def test_with_trained_dict(self):
        dictionary = train_dictionary(SAMPLES)
        self.assertGreater(len(dictionary), 0)
        new_msg = (
            '{"ts":"2026-10-04T10:05:00Z","level":"INFO","service":"auth",'
            '"event":"login_success","user_id":555001,"latency_ms":39}'
        ).encode()
        blob = self.roundtrip(new_msg, dictionary)
        self.assertTrue(blob.startswith(b"DZC1"))

    def test_empty_input_with_and_without_dict(self):
        blob_no = self.roundtrip(b"")
        self.assertEqual(len(blob_no), 4 + 1 + 8)  # magic + 空id + checksum
        dictionary = train_dictionary(SAMPLES)
        self.roundtrip(b"", dictionary)
        self.assertEqual(decompress(blob_no), b"")

    def test_shorter_than_min_match(self):
        for data in (b"", b"a", b"ab", b"abc"):
            self.roundtrip(data, train_dictionary(SAMPLES))


class TrainingEdgeCaseTest(unittest.TestCase):
    def test_no_samples(self):
        dictionary = train_dictionary([])
        self.assertEqual(dictionary, b"")
        blob = compress(b"hello", dictionary)
        self.assertEqual(decompress(blob), b"hello")

    def test_single_sample_too_few(self):
        # 只有一条样本，任何子串的文档频率都不到 2，训练结果应为空字典。
        dictionary = train_dictionary([SAMPLES[0]])
        self.assertEqual(dictionary, b"")

    def test_two_short_identical_samples(self):
        dictionary = train_dictionary([b"ab", b"ab"])
        self.assertEqual(dictionary, b"")  # 短于最小匹配，无收益候选

    def test_highly_repetitive_samples(self):
        sample = b'{"event":"login_success","service":"auth"}' * 4
        dictionary = train_dictionary([sample] * 200)
        self.assertGreater(len(dictionary), 0)
        blob = compress(sample, dictionary, dict_id_for(dictionary))
        # 高度重复内容应被压到远小于原文。
        self.assertLess(len(blob), len(sample) // 2)
        loader = lambda wanted: dictionary  # noqa: E731
        self.assertEqual(decompress(blob, loader), sample)

    def test_dict_mismatch_data(self):
        # 字典与数据完全不匹配：不应崩，解压正确，体积只有固定头开销。
        dictionary = train_dictionary(SAMPLES)
        dict_id = dict_id_for(dictionary)
        data = os.urandom(300)
        blob = compress(data, dictionary, dict_id)
        out = decompress(blob, lambda wanted: dictionary)
        self.assertEqual(out, data)
        # 开销上界 = 无字典体积 + dict_id 长度 + 少量余量（误匹配被抑制）。
        plain = compress(data)
        self.assertLessEqual(len(blob), len(plain) + len(dict_id) + 4)


class PersistenceTest(unittest.TestCase):
    def test_store_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DictStore(tmp)
            content = train_dictionary(SAMPLES)
            dict_id = store.save(content)
            # 用一个全新的 store 实例读取，验证持久化。
            reloaded = DictStore(tmp).load(dict_id)
            self.assertEqual(reloaded, content)
            self.assertEqual(DictStore(tmp).latest_id(), dict_id)
            # 幂等保存。
            self.assertEqual(store.save(content), dict_id)
            self.assertEqual(DictStore(tmp).list_ids(), [dict_id])

    def test_old_dict_remains_readable_after_retrain(self):
        """字典变化处理：重新训练产生新 id，旧字典保留，旧压缩块仍可解压。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = DictStore(tmp)

            id_v1 = store.save(train_dictionary(SAMPLES))
            blob_v1 = compress(SAMPLES[0].encode(), store.load(id_v1), id_v1)

            # 新一批样本（结构漂移），重新训练得到不同字典。
            new_samples = ["<log level=info node=n01> request /api/v2/users handled"] * 30
            id_v2 = store.save(train_dictionary(new_samples))
            self.assertNotEqual(id_v1, id_v2)

            blob_v2 = compress(
                new_samples[0].encode(), store.load(id_v2), id_v2)

            # 仓库里两版字典并存，新旧数据各自按头部 id 解压。
            self.assertEqual(set(store.list_ids()), {id_v1, id_v2})
            self.assertEqual(decompress(blob_v1, store.load), SAMPLES[0].encode())
            self.assertEqual(decompress(blob_v2, store.load),
                             new_samples[0].encode())

    def test_missing_dict_raises(self):
        dictionary = train_dictionary(SAMPLES)
        blob = compress(SAMPLES[1].encode(), dictionary, dict_id_for(dictionary))
        with tempfile.TemporaryDirectory() as tmp:
            store = DictStore(tmp)
            with self.assertRaises(DictionaryNotFoundError):
                decompress(blob, store.load)

    def test_wrong_dict_content_detected(self):
        """压缩块记录了 id=A，但仓库给了另一份内容：必须由校验和拦截。"""
        dict_a = train_dictionary(SAMPLES)
        id_a = dict_id_for(dict_a)
        dict_b = train_dictionary(["<log level=info node=n01>"] * 40)
        blob = compress(SAMPLES[2].encode(), dict_a, id_a)
        # 错误字典会被拦截：要么偏移越界（CorruptDataError），要么校验和失败
        # （IntegrityError 是 CorruptDataError 的子类）。
        with self.assertRaises(CorruptDataError):
            decompress(blob, lambda wanted: dict_b)

    def test_corrupted_blob_detected(self):
        blob = bytearray(compress(SAMPLES[3].encode()))
        blob[-3] ^= 0xFF
        with self.assertRaises((IntegrityError, CorruptDataError)):
            decompress(bytes(blob))

    def test_garbage_input(self):
        with self.assertRaises(CorruptDataError):
            decompress(b"not a compressed blob at all")
        with self.assertRaises(CorruptDataError):
            decompress(b"")

    def test_store_rejects_corrupt_dict_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DictStore(tmp)
            dict_id = store.save(train_dictionary(SAMPLES))
            path = os.path.join(tmp, dict_id + ".dict")
            with open(path, "r+b") as fh:
                fh.seek(-1, os.SEEK_END)
                fh.write(b"X")
            with self.assertRaises(CorruptDataError):
                DictStore(tmp).load(dict_id)


class CompressionRatioTest(unittest.TestCase):
    def test_dict_beats_no_dict_on_similar_messages(self):
        dictionary = train_dictionary(SAMPLES * 30)
        eval_set = SAMPLES
        plain = sum(len(compress(m.encode())) for m in eval_set)
        with_dict = sum(
            len(compress(m.encode(), dictionary, dict_id_for(dictionary)))
            for m in eval_set
        )
        self.assertLess(with_dict, plain)
        self.assertLess(with_dict, sum(len(m) for m in eval_set))


if __name__ == "__main__":
    unittest.main(verbosity=2)
