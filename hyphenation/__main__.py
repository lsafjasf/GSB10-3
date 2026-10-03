"""CLI demo:  python3 -m hyphenation <lang> <text> [text ...]

Prints break points and the text with soft hyphens (shown as U+00AD).
"""
import sys

from .core import Hyphenator


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    hyph = Hyphenator(argv[1])
    for text in argv[2:]:
        points = hyph.break_points(text)
        print(f"{text}\n  breaks: {points}\n  hyphenated: {hyph.hyphenate(text)!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
