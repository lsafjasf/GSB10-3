"""命令行入口：python3 -m failcluster <failures.json> [--json] [--threshold 0.75]

输入 JSON 格式（数组）：
[
  {
    "case_id": "TC-001",
    "test": "tests/payment/test_checkout.py::test_pay",
    "error_type": "AssertionError",
    "message": "expected 200 got 500",
    "phase": "call",                      // 可选：setup/call/teardown
    "traceback": [                        // 可选，从外到内
      {"file": "tests/payment/test_checkout.py", "function": "test_pay", "line": 42},
      {"file": "src/payment/gateway.py", "function": "charge", "line": 88}
    ]
  }
]
"""

import argparse
import json
import sys

from .cluster import cluster_failures
from .models import FailureCase
from .report import build_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="failcluster", description="回归失败归因聚类"
    )
    parser.add_argument("input", help="失败用例 JSON 文件路径（'-' 表示标准输入）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    parser.add_argument(
        "--threshold", type=float, default=0.75, help="软合并相似度阈值（默认 0.75）"
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    records = json.loads(raw)
    if isinstance(records, dict):
        records = records.get("failures", [])
    cases = [FailureCase.from_dict(r) for r in records]

    clusters = cluster_failures(cases, merge_threshold=args.threshold)
    report = build_report(cases, clusters)

    print(report.to_json() if args.json else report.to_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
