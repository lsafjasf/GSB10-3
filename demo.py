"""演示：词典热更新前后切分对比 + 词频影响复现 + 原子性验证。

运行：python3 demo.py            # 打印到终端
     python3 demo.py | tee demo_output.txt
"""

from segmenter import Dictionary, Segmenter


def show(title):
    print("\n=== %s ===" % title)


def main():
    # ---------- 1. 词典热更新前后对比 ----------
    show("1. 词典热更新前后切分对比")
    dict_v1 = Dictionary(
        {
            "我": 800, "爱": 200, "北京": 500, "天安门": 300,
            "概念": 150, "火": 400, "了": 900, "的": 1000,
            "年轻人": 400, "开始": 300, "用": 500,
        },
        version="v1",
    )
    # v2：新增网络新词，其余不变
    dict_v2 = Dictionary(
        dict(dict_v1.freq, 元宇宙=8000, 内卷=6000, 躺平=5000, 破防=4000),
        version="v2",
    )
    seg = Segmenter(dict_v1)
    sentences = [
        "元宇宙概念火了",
        "年轻人开始内卷",
        "我破防了",
        "躺平的年轻人",
        "我爱北京天安门",  # 不含新词的对照句：更新前后必须一致
    ]
    print("%-16s | %-28s | %-28s" % ("输入", "更新前(v1)", "更新后(v2)"))
    print("-" * 82)
    before_all = [seg.segment(s) for s in sentences]  # 全部用 v1 切完
    seg.update(dict_v2)                               # 原子热更新，立即生效
    after_all = [seg.segment(s) for s in sentences]
    for s, before, after in zip(sentences, before_all, after_all):
        print("%-16s | %-28s | %-28s"
              % (s, "/".join(before), "/".join(after)))

    # ---------- 2. 原子性：切分中途更新不影响本次结果 ----------
    show("2. 原子更新断言（切分中途换词典）")
    seg2 = Segmenter(dict_v1)
    target = "元宇宙概念火了"
    pure_v1 = seg2.segment(target)

    def swap_midway(phase, s):
        if phase == "after_snapshot":
            s.update(dict_v2)  # 在本次切分中途热更新

    seg2.test_hook = swap_midway
    inflight = seg2.segment(target)
    print("纯 v1 结果        : %s" % "/".join(pure_v1))
    print("中途更新时的结果  : %s" % "/".join(inflight))
    assert inflight == pure_v1, "切分中途更新污染了结果！"
    print("断言通过：进行中的切分完全使用开始时的快照，不受更新影响")
    print("更新后新切分      : %s （新版本已生效）" % "/".join(seg2.segment(target)))

    # ---------- 3. 词频影响切分：同一输入、不同词频、结果可复现 ----------
    show("3. 词频影响切分结果（可复现）")
    sentence = "研究生命起源"
    for freq in (10, 1000, 100000, 10000000):
        d = Dictionary({"研究": 100, "生命": 100, "起源": 100,
                        "研究生": freq})
        tokens = Segmenter(d).segment(sentence)
        print("研究生词频=%-9d -> %s" % (freq, "/".join(tokens)))

    # ---------- 4. 边界用例 ----------
    show("4. 边界用例")
    seg3 = Segmenter(dict_v2)
    cases = [
        ("空串", ""),
        ("全空白", "   \t "),
        ("全标点", "！！！？？？，。"),
        ("全未登录字", "龘靐齉爩"),
        ("ASCII 串", "订单A2024-10已发货"),
        ("中英混合", "我用iPhone15刷xhs"),
    ]
    for name, s in cases:
        print("%-10s %r -> %s" % (name, s, "/".join(seg3.segment(s)) or "(空)"))
    long_text = "我爱北京天安门" * 20000
    import time
    t0 = time.time()
    tokens = seg3.segment(long_text)
    print("超长句     %d 字符 -> %d 个 token，耗时 %.2fs"
          % (len(long_text), len(tokens), time.time() - t0))


if __name__ == "__main__":
    main()
