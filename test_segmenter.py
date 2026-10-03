"""segmenter 自测：切分正确性、原子热更新、词频复现、边界用例。

运行：python3 -m unittest test_segmenter -v
"""

import threading
import time
import unittest

from segmenter import Dictionary, Segmenter


def make_base_dict(version="v1"):
    return Dictionary(
        {
            "我": 800, "爱": 200, "北京": 500, "天安门": 300,
            "研究": 100, "生命": 100, "起源": 100,
            "概念": 150, "火": 400, "了": 900,
            "小红书": 600, "刷": 300, "用": 500,
        },
        version=version,
    )


class TestBasicSegmentation(unittest.TestCase):
    def setUp(self):
        self.seg = Segmenter(make_base_dict())

    def test_basic_cut(self):
        self.assertEqual(self.seg.segment("我爱北京天安门"),
                         ["我", "爱", "北京", "天安门"])

    def test_oov_does_not_degenerate_sentence(self):
        # 含未登录字，但词典词必须完整切出，不能整句退化为单字
        tokens = self.seg.segment("我爱北京龘天安门")
        self.assertEqual(tokens, ["我", "爱", "北京", "龘", "天安门"])

    def test_ascii_run_merged(self):
        tokens = self.seg.segment("我用iPhone15刷小红书")
        self.assertEqual(tokens, ["我", "用", "iPhone15", "刷", "小红书"])

    def test_whitespace_dropped(self):
        self.assertEqual(self.seg.segment("我 爱  北京"), ["我", "爱", "北京"])

    def test_from_lines(self):
        d = Dictionary.from_lines(["北京 500", "# 注释", "天安门", "bad x"],
                                  version="t")
        self.assertEqual(d.freq["北京"], 500)
        self.assertEqual(d.freq["天安门"], 1)
        self.assertEqual(d.freq["bad"], 1)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.seg = Segmenter(make_base_dict())

    def test_empty_string(self):
        self.assertEqual(self.seg.segment(""), [])

    def test_whitespace_only(self):
        self.assertEqual(self.seg.segment("   \t\n "), [])

    def test_all_punctuation(self):
        tokens = self.seg.segment("！！！？？？，。")
        self.assertEqual(tokens, list("！！！？？？，。"))

    def test_all_oov(self):
        tokens = self.seg.segment("龘靐齉爩")
        self.assertEqual(tokens, ["龘", "靐", "齉", "爩"])

    def test_mixed_oov_and_ascii(self):
        tokens = self.seg.segment("订单A2024-10已发货")
        self.assertIn("A2024-10", tokens)

    def test_very_long_sentence(self):
        text = "我爱北京天安门" * 20000  # 14 万字符
        start = time.time()
        tokens = self.seg.segment(text)
        elapsed = time.time() - start
        self.assertEqual(tokens, ["我", "爱", "北京", "天安门"] * 20000)
        self.assertLess(elapsed, 10.0, "超长句切分超时")
        print("\n[long] 140000 chars -> %d tokens in %.2fs"
              % (len(tokens), elapsed))


class TestFrequencyReproducible(unittest.TestCase):
    """同一输入在不同词频下切分不同，且结果可精确复现。"""

    SENTENCE = "研究生命起源"

    def _cut(self, postgrad_freq):
        d = Dictionary({"研究": 100, "生命": 100, "起源": 100,
                        "研究生": postgrad_freq})
        return Segmenter(d).segment(self.SENTENCE)

    def test_low_freq_prefers_split(self):
        self.assertEqual(self._cut(10), ["研究", "生命", "起源"])

    def test_high_freq_prefers_whole_word(self):
        self.assertEqual(self._cut(10_000_000), ["研究生", "命", "起源"])

    def test_results_are_deterministic(self):
        for _ in range(5):
            self.assertEqual(self._cut(10), ["研究", "生命", "起源"])
            self.assertEqual(self._cut(10_000_000), ["研究生", "命", "起源"])


class TestAtomicUpdate(unittest.TestCase):
    def setUp(self):
        self.d1 = make_base_dict("v1")
        self.d2 = Dictionary(dict(self.d1.freq, 元宇宙=8000), version="v2")
        self.seg = Segmenter(self.d1)
        self.sentence = "元宇宙概念火了"

    def test_update_takes_effect_for_new_segments(self):
        before = self.seg.segment(self.sentence)
        self.assertEqual(before, ["元", "宇", "宙", "概念", "火", "了"])
        self.seg.update(self.d2)
        after = self.seg.segment(self.sentence)
        self.assertEqual(after, ["元宇宙", "概念", "火", "了"])
        self.assertEqual(self.seg.dictionary.version, "v2")

    def test_inflight_segment_uses_single_snapshot(self):
        """切分中途发生更新，本次结果必须完全来自旧版本。"""
        expected_old = Segmenter(self.d1).segment(self.sentence)
        calls = []

        def hook(phase, seg):
            calls.append(phase)
            if phase == "after_snapshot":
                seg.update(self.d2)  # 在切分中途热更新词典

        self.seg.test_hook = hook
        result = self.seg.segment(self.sentence)
        self.assertEqual(result, expected_old,
                         "切分中途更新词典污染了正在进行的结果")
        self.assertEqual(calls, ["after_snapshot", "before_return"])
        # 更新已生效：下一次切分用新版本
        self.assertEqual(self.seg.segment(self.sentence),
                         Segmenter(self.d2).segment(self.sentence))

    def test_concurrent_update_never_mixes_versions(self):
        """多线程压测：结果要么完全是 v1，要么完全是 v2，绝不混杂。"""
        pure_v1 = Segmenter(self.d1).segment(self.sentence)
        pure_v2 = Segmenter(self.d2).segment(self.sentence)
        self.assertNotEqual(pure_v1, pure_v2)
        stop = threading.Event()
        flips = [0]

        def flipper():
            d = self.d2
            while not stop.is_set():
                self.seg.update(d)
                d = self.d1 if d is self.d2 else self.d2
                flips[0] += 1
                time.sleep(0.001)  # 每 1ms 翻转一次，模拟真实热更新节奏

        t = threading.Thread(target=flipper)
        t.start()
        seen = set()
        deadline = time.time() + 0.6
        rounds = 0
        while time.time() < deadline:
            result = tuple(self.seg.segment(self.sentence))
            self.assertIn(result, (tuple(pure_v1), tuple(pure_v2)),
                          "切分结果混杂了两个词典版本: %s" % (result,))
            seen.add(result)
            rounds += 1
        stop.set()
        t.join()
        self.assertGreater(flips[0], 0)
        self.assertEqual(seen, {tuple(pure_v1), tuple(pure_v2)},
                         "压测中应同时观察到 v1 与 v2 的纯净结果")
        print("\n[atomic] %d 次词典翻转下 %d 次切分全部纯净" % (flips[0], rounds))


if __name__ == "__main__":
    unittest.main(verbosity=2)
