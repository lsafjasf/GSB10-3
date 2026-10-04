# -*- coding: utf-8 -*-
"""生成 data/polyphonic_samples.md：多音字候选样例（含优先级依据）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinyin_lib import candidates, pinyin  # noqa: E402

SAMPLE_CHARS = ["长", "重", "行", "乐", "的", "着", "和", "差", "宿", "少",
                "教", "为", "好", "中", "发", "说", "看", "数", "种", "地"]

DEMO_SENTENCES = ["重庆银行行长", "长大", "音乐", "目的", "着急", "会计",
                  "头发", "游说", "看守", "种子", "大夫", "睡觉", "重量",
                  "勉强", "星宿", "系鞋带", "便宜", "参差", "绿林", "脉脉"]

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "polyphonic_samples.md")


def main():
    lines = [
        "# 多音字候选样例",
        "",
        "候选排序依据：语料频率排名（rank 1 = 最常用），词表示例为该读音",
        "在词组模式下的上下文证据。本文件由 `tools/dump_samples.py` 生成。",
        "",
        "## 候选列表（带声调）",
        "",
    ]
    for ch in SAMPLE_CHARS:
        lines.append("### %s" % ch)
        lines.append("")
        for item in candidates(ch):
            examples = ("；词表示例：" + "、".join(item["examples"])
                        if item["examples"] else "")
            lines.append("%d. **%s**（%s%s）"
                         % (item["rank"], item["pinyin"], item["basis"], examples))
        lines.append("")

    lines += [
        "## 词组消歧示例（词组模式 vs 单字模式）",
        "",
        "| 文本 | 词组模式 | 单字模式 |",
        "| --- | --- | --- |",
    ]
    for text in DEMO_SENTENCES:
        phrase = " ".join(pinyin(text, mode="phrase"))
        char = " ".join(pinyin(text, mode="char"))
        lines.append("| %s | %s | %s |" % (text, phrase, char))
    lines.append("")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
