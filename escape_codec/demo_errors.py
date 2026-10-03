#!/usr/bin/env python3
"""错误定位演示：逐个解析 examples/ 下的坏样本，打印行号/列号与出错片段。

用法：python3 demo_errors.py [examples_dir]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from escape import EscapeError, decode


def show_caret(data: bytes, offset: int) -> str:
    """生成指向出错位置的 ^ 指示行（按字节列对齐）。"""
    line_start = data.rfind(b"\n", 0, offset) + 1
    line_end = data.find(b"\n", offset)
    if line_end == -1:
        line_end = len(data)
    line_text = data[line_start:line_end].decode("ascii", "backslashreplace")
    caret = " " * (offset - line_start) + "^"
    return f"    {line_text}\n    {caret}"


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
    failures = 0
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        data = open(path, "rb").read()
        print(f"== {name} ==")
        try:
            decode(data)
        except EscapeError as e:
            print(f"  EscapeError: {e}")
            print(show_caret(data, e.offset))
        else:
            print("  !! 未报错（不符合预期）")
            failures += 1
        print()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
