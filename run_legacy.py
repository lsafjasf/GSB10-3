"""用法: python3 run_legacy.py <输入csv> <输出目录>"""
import sys

from legacy_pipeline import process_file


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    report, outbox = process_file(sys.argv[1], sys.argv[2])
    print(report, end="")
    print(f"[legacy] wrote report.txt + outbox.log to {sys.argv[2]} "
          f"({len(outbox.splitlines())} notifications)", file=sys.stderr)


if __name__ == "__main__":
    main()
