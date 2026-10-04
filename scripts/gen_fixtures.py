#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成边界用例 PDF 到 fixtures/ 目录。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fixtures import all_fixtures


def main():
    outdir = os.path.join(os.path.dirname(__file__), "..", "fixtures")
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    for name, desc, data in all_fixtures():
        path = os.path.join(outdir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        print("%-32s %6d 字节  %s" % (name, len(data), desc))
    print("已生成到 %s" % outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
