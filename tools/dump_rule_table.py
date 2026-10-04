# -*- coding: utf-8 -*-
"""由字表生成规则表 data/rule_table.tsv（人工核对后入库，测试逐项对拍）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinyin_lib import CHAR_TABLE, to_plain, to_tone_mark  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "rule_table.tsv")


def main():
    lines = ["char\tpinyin_numbered\tpinyin_tone\tpinyin_plain"]
    for ch in sorted(CHAR_TABLE):
        for reading in CHAR_TABLE[ch]:
            lines.append("\t".join([ch, reading, to_tone_mark(reading), to_plain(reading)]))
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote %d rows -> %s" % (len(lines) - 1, OUT))


if __name__ == "__main__":
    main()
