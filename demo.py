"""演示：词典热更新前后切分对比 + 原子更新断言 + 词频影响复现。

运行: python3 demo.py
"""

import os
import threading

from tokenizer import Dictionary, Segmenter

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    return Dictionary.from_file(os.path.join(HERE, name), version=name)


def show(seg, text):
    tokens = seg.segment_tokens(text)
    parts = [f"{t}{'*' if unk else ''}" for t, unk in tokens]
    return " / ".join(parts)  # 词后带 * 表示未登录词回退


def main():
    d1 = load("dict_v1.txt")
    d2 = load("dict_v2.txt")
    seg = Segmenter(d1)

    sentences = [
        "区块链技术发展很快",
        "南京市长江大桥",
        "中国人民银行",
        "大学生",
        "我爱北京天安门",
    ]

    print("=" * 72)
    print("一、词典热更新前后切分对比（* = 未登录词回退）")
    print("=" * 72)
    before = {t: show(seg, t) for t in sentences}
    seg.update(d2)  # 原子切换，正在进行的切分不受影响
    after = {t: show(seg, t) for t in sentences}
    for t in sentences:
        changed = "变化" if before[t] != after[t] else "不变"
        print(f"\n输入: {t}    [{changed}]")
        print(f"  更新前({d1.version}): {before[t]}")
        print(f"  更新后({d2.version}): {after[t]}")

    print()
    print("=" * 72)
    print("二、词频影响切分结果（同一输入，仅词频不同，可复现）")
    print("=" * 72)
    text = "大学生"
    for name in ("dict_freq_a.txt", "dict_freq_b.txt"):
        s = Segmenter(load(name))
        freq = s.dictionary.words["大学生"]
        print(f"  {name}（大学生 freq={freq:>6}）: {show(s, text)}")

    print()
    print("=" * 72)
    print("三、原子更新断言：并发读写，结果不允许出现版本撕裂")
    print("=" * 72)
    expected = {}
    for t in sentences:
        r1 = tuple(Segmenter(d1).segment(t))
        r2 = tuple(Segmenter(d2).segment(t))
        expected[t] = {r1, r2}

    seg2 = Segmenter(d1)
    stop = threading.Event()
    stats = {"rounds": 0, "v1_hits": 0, "v2_hits": 0, "torn": 0}

    def writer():
        i = 0
        while not stop.is_set():
            seg2.update(d1 if i % 2 == 0 else d2)
            i += 1

    def reader():
        while stats["rounds"] < 20000 and not stop.is_set():
            for t in sentences:
                got = tuple(seg2.segment(t))
                if got not in expected[t]:
                    stats["torn"] += 1
                    stop.set()
                    return
                if got == tuple(Segmenter(d1).segment(t)):
                    stats["v1_hits"] += 1
                else:
                    stats["v2_hits"] += 1
            stats["rounds"] += 1

    threads = [threading.Thread(target=writer)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads[1:]:
        th.join()
    stop.set()
    for th in threads:
        th.join()

    total = stats["v1_hits"] + stats["v2_hits"]
    print(f"  并发切分总次数: {total}")
    print(f"  完整命中 v1 结果: {stats['v1_hits']} 次")
    print(f"  完整命中 v2 结果: {stats['v2_hits']} 次")
    print(f"  版本撕裂（两版混合的非法结果）: {stats['torn']} 次")
    assert stats["torn"] == 0 and total > 0
    print("  断言通过：每次切分要么整体是 v1，要么整体是 v2，无撕裂。")

    print()
    print("=" * 72)
    print("四、边界用例")
    print("=" * 72)
    print(f"  空串: {seg.segment('')}")
    print(f"  全标点 「！！！？？？」: {show(seg, '！！！？？？')}")
    long_text = "我爱北京天安门。" * 15000
    toks = seg.segment(long_text)
    print(f"  超长句子（{len(long_text)} 字符）: 切出 {len(toks)} 个词，"
          f"前 5 个 = {toks[:5]}")
    oov = "元宇宙" + "龥" * 100
    print(f"  大量未登录词（102 字）: {seg.segment(oov)}")


if __name__ == "__main__":
    main()
