#!/usr/bin/env python3
"""生成对拍数据文件 data/cases.json（确定性，可重复生成）。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.cases import build_cases

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "cases.json")


def main() -> None:
    cases = build_cases()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"idle_timeout": 60.0, "cases": cases}, fh,
                  indent=2, sort_keys=True)
        fh.write("\n")
    total = sum(len(c["records"]) for c in cases)
    print(f"wrote {OUT}: {len(cases)} cases, {total} records")


if __name__ == "__main__":
    main()
