"""CLI: python3 -m hyphenator <lang> <word> [--hyphen CHAR]"""
import argparse

from .core import Hyphenator


def main():
    parser = argparse.ArgumentParser(
        prog="hyphenator", description="Multilingual hyphenation (stdlib only)"
    )
    parser.add_argument("lang", help="language code: en, de, zh, ja, ko")
    parser.add_argument("word", help="word or text to hyphenate")
    parser.add_argument("--hyphen", default="-", help="character used for display")
    args = parser.parse_args()

    h = Hyphenator(args.lang)
    print("language:   ", args.lang)
    print("breakpoints:", h.breakpoints(args.word))
    print("hyphenated: ", h.hyphenate(args.word, hyphen=args.hyphen))
    violations = h.validate(args.word)
    print("violations: ", violations if violations else "none")


if __name__ == "__main__":
    main()
