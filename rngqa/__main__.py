"""
命令行：
  python3 -m rngqa                 跑固定数据自测，打印结果表
  python3 -m rngqa --file out.bin  评估外部随机源转储的字节文件
"""

import sys

from .selftest import run, render_markdown, render_details
from .quality import assess


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--file":
        if len(argv) < 3:
            print("usage: python3 -m rngqa --file <binary-file>")
            return 2
        with open(argv[2], "rb") as f:
            data = f.read()
        report = assess(data, argv[2])
        print(f"sample: {report.sample}  bytes: {report.n_bytes}")
        for c in report.results:
            extra = f" — {c.reason}" if c.reason else ""
            print(f"[{c.verdict}] {c.name}{extra}: {c.statistic}")
        print(f"OVERALL: {report.verdict}")
        return 0 if report.verdict != "FAIL" else 1

    reports, failures = run()
    print(render_markdown(reports))
    print()
    print(render_details(reports))
    print()
    if failures:
        print("SELF-TEST FAILED:")
        for f_ in failures:
            print(" -", f_)
        return 1
    print(f"SELF-TEST PASSED: {len(reports)} fixed samples, assertions OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
