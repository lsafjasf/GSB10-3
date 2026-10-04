#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建对拍脚本：顺序扫描重建对象偏移，与文件内索引声明逐条比对。

用法: python3 scripts/verify_rebuild.py FILE [FILE ...] [--strict]
退出码: 0 全部一致；1 存在不一致；2 用法错误/strict 下索引不可用。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdflite import Document
from pdflite import verify as verify_mod


def main(argv):
    strict = "--strict" in argv
    files = [a for a in argv if a != "--strict"]
    if not files:
        print(__doc__)
        return 2
    worst = 0
    for path in files:
        print("=" * 78)
        print("对拍文件: %s" % path)
        doc = Document.load(path)
        rep = verify_mod.compare(doc)
        print(verify_mod.format_report(rep))
        code = 0 if rep.ok else 1
        if strict and not doc.index_ok:
            code = 2
        worst = max(worst, code)
        print()
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
