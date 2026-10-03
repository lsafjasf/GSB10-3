"""tokenizer 自测：原子更新断言、词频复现、边界用例。

运行: python3 test_tokenizer.py  （或 python3 -m unittest -v）
"""

import os
import random
import threading
import unittest

from tokenizer import Dictionary, Segmenter

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    return Dictionary.from_file(os.path.join(HERE, name), version=name)


class SegmentationTest(unittest.TestCase):
    def setUp(self):
        self.seg = Segmenter(load("dict_v1.txt"))

    def test_basic(self):
        self.assertEqual(
            self.seg.segment("我爱北京天安门"),
            ["我", "爱", "北京", "天安门"],
        )

    def test_empty_string(self):
        self.assertEqual(self.seg.segment(""), [])

    def test_all_punctuation(self):
        text = "！！！？？？。。。"
        tokens = self.seg.segment(text)
        self.assertEqual(tokens, list(text))  # 标点逐字成词

    def test_whitespace_skipped(self):
        self.assertEqual(self.seg.segment("我 爱\t北京\n天安门"), ["我", "爱", "北京", "天安门"])

    def test_oov_cjk_merged_not_single_chars(self):
        # 「元宇宙」不在词典中：应合并为一个词，而非退化成单字
        tokens = self.seg.segment("元宇宙")
        self.assertEqual(tokens, ["元宇宙"])
        tokens = self.seg.segment("我爱元宇宙")
        self.assertEqual(tokens, ["我", "爱", "元宇宙"])

    def test_oov_ascii_and_digits_merged(self):
        tokens = self.seg.segment("hello2026")
        self.assertEqual(tokens, ["hello2026"])

    def test_oov_classes_not_mixed(self):
        # 汉字与 ASCII 属不同类别，不合并
        tokens = self.seg.segment("元宇宙abc")
        self.assertEqual(tokens, ["元宇宙", "abc"])

    def test_many_oov(self):
        # 大量未登录字：整段合并为一个词，不逐字碎裂
        rng = random.Random(42)
        text = "".join(chr(rng.randint(0x9F00, 0x9FFF)) for _ in range(5000))
        self.assertFalse(any(w in self.seg.dictionary.words for w in text))
        self.assertEqual(self.seg.segment(text), [text])

    def test_long_sentence(self):
        unit = "我爱北京天安门。"
        text = unit * 15000  # 12 万字符
        tokens = self.seg.segment(text)
        self.assertEqual(len(tokens), 15000 * 5)
        self.assertEqual(tokens[:5], ["我", "爱", "北京", "天安门", "。"])
        self.assertEqual(tokens[-5:], ["我", "爱", "北京", "天安门", "。"])

    def test_long_unknown_run(self):
        text = "龥" * 100000  # 10 万未登录字
        self.assertEqual(self.seg.segment(text), [text])


class FrequencyTest(unittest.TestCase):
    """词频影响切分结果，且可复现。"""

    def test_same_input_different_freq_different_result(self):
        text = "大学生"
        seg_a = Segmenter(load("dict_freq_a.txt"))  # 大学生 高频
        seg_b = Segmenter(load("dict_freq_b.txt"))  # 大学生 低频，其余相同
        result_a = seg_a.segment(text)
        result_b = seg_b.segment(text)
        self.assertEqual(result_a, ["大学生"])
        self.assertEqual(result_b, ["大学", "生"])
        self.assertNotEqual(result_a, result_b)

    def test_reproducible(self):
        # 同一词典版本切同一输入，结果永远一致
        seg = Segmenter(load("dict_v1.txt"))
        first = seg.segment("南京市长江大桥")
        for _ in range(50):
            self.assertEqual(seg.segment("南京市长江大桥"), first)


class HotUpdateTest(unittest.TestCase):
    def setUp(self):
        self.d1 = load("dict_v1.txt")
        self.d2 = load("dict_v2.txt")
        self.seg = Segmenter(self.d1)

    def test_update_takes_effect_atomically(self):
        text = "区块链技术发展很快"
        before = self.seg.segment(text)
        self.assertEqual(before, ["区", "块", "链", "技术", "发展", "很", "快"])
        self.seg.update(self.d2)
        after = self.seg.segment(text)
        self.assertEqual(after, ["区块链", "技术", "发展", "很", "快"])
        self.assertNotEqual(before, after)

    def test_no_switch_mid_segmentation(self):
        """原子更新断言：并发更新期间，每次切分结果必须完整属于某一个版本，
        不允许出现两个版本混合的“撕裂”结果。"""
        texts = [
            "区块链技术发展很快",
            "南京市长江大桥",
            "中国人民银行",
            "大学生",
        ]
        # 预先离线计算两个版本的期望结果
        expected = {}
        for t in texts:
            r1 = tuple(Segmenter(self.d1).segment(t))
            r2 = tuple(Segmenter(self.d2).segment(t))
            assert r1 != r2, f"测试前提失败：{t} 在两个版本下结果相同"
            expected[t] = {r1, r2}

        seg = Segmenter(self.d1)
        errors = []
        stop = threading.Event()

        def writer():
            dics = [self.d1, self.d2]
            i = 0
            while not stop.is_set():
                seg.update(dics[i % 2])
                i += 1

        def reader():
            try:
                for _ in range(3000):
                    for t in texts:
                        got = tuple(seg.segment(t))
                        if got not in expected[t]:
                            errors.append((t, got))
                            stop.set()
                            return
            except Exception as e:  # 切分过程不得抛异常
                errors.append(("exception", repr(e)))
                stop.set()

        threads = [threading.Thread(target=writer)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads[1:]:
            th.join()
        stop.set()
        for th in threads:
            th.join()

        self.assertEqual(errors, [])

    def test_snapshot_isolated_from_later_update(self):
        # segment 内部的词典快照不受之后的 update 影响：
        # 用超长文本放大窗口，在另一线程切分途中多次更新，
        # 结果仍必须等于某一版本的完整结果。
        text = "区块链技术发展很快" * 2000
        r1 = tuple(Segmenter(self.d1).segment(text))
        r2 = tuple(Segmenter(self.d2).segment(text))
        self.assertNotEqual(r1, r2)

        seg = Segmenter(self.d1)
        results = []

        def reader():
            results.append(tuple(seg.segment(text)))

        th = threading.Thread(target=reader)
        th.start()
        for _ in range(200):
            seg.update(self.d2)
            seg.update(self.d1)
        th.join()
        self.assertIn(results[0], (r1, r2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
