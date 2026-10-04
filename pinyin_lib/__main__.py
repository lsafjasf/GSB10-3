"""命令行入口。

用法：
  python3 -m pinyin_lib 重庆银行
  python3 -m pinyin_lib --mode char --style plain 重庆银行
  python3 -m pinyin_lib --heteronym 长大
  python3 -m pinyin_lib --candidates 长
"""

import argparse

from .core import pinyin, explain


def main(argv=None):
    parser = argparse.ArgumentParser(description="拼音转换（词组消歧 / 多音字候选）")
    parser.add_argument("text", nargs="?", help="要转换的文本")
    parser.add_argument("--mode", choices=["phrase", "char"], default="phrase",
                        help="phrase=词组优先（默认），char=单字逐字转换")
    parser.add_argument("--style", choices=["tone", "plain"], default="tone",
                        help="tone=带声调（默认），plain=不带声调")
    parser.add_argument("--heteronym", action="store_true",
                        help="每个汉字输出全部候选读音")
    parser.add_argument("--candidates", metavar="字",
                        help="输出某个多音字的候选列表与排序依据")
    args = parser.parse_args(argv)

    if args.candidates:
        print(explain(args.candidates, style=args.style))
        return
    if not args.text:
        parser.error("缺少要转换的文本")

    for item in pinyin(args.text, mode=args.mode, style=args.style,
                       heteronym=args.heteronym):
        if isinstance(item, list):
            print(" / ".join(item))
        else:
            print(item)


if __name__ == "__main__":
    main()
