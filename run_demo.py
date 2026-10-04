#!/usr/bin/env python3
"""一键运行 demo：生成数据 -> 聚类 -> 输出文本与 JSON 结果。"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from failcluster import cluster_from_dicts  # noqa: E402

DEMO = ROOT / "demo"


def main() -> int:
    subprocess.run([sys.executable, str(DEMO / "gen_demo_data.py")], check=True)
    records = json.loads((DEMO / "failures.json").read_text(encoding="utf-8"))

    report = cluster_from_dicts(records)

    text = report.to_text()
    (DEMO / "result.txt").write_text(text, encoding="utf-8")
    (DEMO / "result.json").write_text(report.to_json(), encoding="utf-8")

    print(text)
    print(f"\n结果已写入 {DEMO / 'result.txt'} 与 {DEMO / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
