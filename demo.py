#!/usr/bin/env python3
"""Demo: step-by-step editor state sequences for tricky inputs.

Run: python3 demo.py
"""
from editor import run

CASES = [
    ("ASCII", "hello",
     [("move_left",), ("move_left",), ("backspace",), ("move_right",),
      ("insert", "L"), ("delete_forward",)]),
    ("combining marks (e + U+0301, a + U+0301 U+0327)", "éa̧x",
     [("move_left",), ("backspace",), ("backspace",), ("move_left",),
      ("delete_forward",)]),
    ("emoji: ZWJ family + skin tone + flag + keycap",
     "👨‍👩‍👧‍👦👍🏽🇨🇳1️⃣",
     [("move_left",), ("backspace",), ("backspace",), ("backspace",),
      ("backspace",), ("insert", "🇺🇸"), ("move_left",), ("move_right",)]),
    ("bidi controls (RLO U+202E / PDF U+202C)", "ab\u202Ecd\u202Cef",
     [("move_left",), ("move_left",), ("backspace",), ("backspace",),
      ("move_left",), ("move_left",), ("delete_forward",)]),
    ("variation selector + Hangul jamo", "✈️각z",
     [("move_left",), ("backspace",), ("backspace",), ("backspace",)]),
]


def main():
    for title, text, ops in CASES:
        print(f"== {title} ==")
        print(f"   input: {text!r}")
        for label, state in run(ops, text):
            print(f"   {label:<22} {state}")
        print()


if __name__ == "__main__":
    main()
