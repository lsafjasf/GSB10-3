"""用 legacy 管线对 data/valid_orders.json 生成对拍期望输出 data/expected_output.jsonl。

用法：python3 scripts/make_golden.py
仅当 legacy 计算逻辑变化时才需要重新生成。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import legacy_pipeline  # noqa: E402


def main():
    with open(os.path.join(ROOT, "data", "valid_orders.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    out_path = os.path.join(ROOT, "data", "expected_output.jsonl")
    legacy_pipeline.run_batch(payload["orders"], out_path)
    print("golden written to", out_path)


if __name__ == "__main__":
    main()
