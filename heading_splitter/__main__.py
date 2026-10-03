"""命令行入口：python3 -m heading_splitter <规范文本文件> [--json]"""

import argparse
import sys

from .report import render_report, report_json
from .source import parse_source_file
from .splitter import FILL, rebuild_source, split_document
from .source import dump_source


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="heading_splitter", description="章节切分与层级还原")
    parser.add_argument("path", help="规范文本文件（文本<TAB>字号<TAB>缩进<TAB>布局）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 对照数据")
    args = parser.parse_args(argv)

    blocks = parse_source_file(args.path)
    result = split_document(blocks)
    assert rebuild_source(result, FILL) == dump_source(blocks), "重建断言失败"
    print(report_json(result) if args.json else render_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
