#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "== 重构前行为快照 =="
python3 tools/dump_behavior.py legacy > /tmp/thresholds_before.json

echo "== 重构后行为快照 =="
python3 tools/dump_behavior.py app > /tmp/thresholds_after.json

echo "== 前后 diff（无输出即一致）=="
diff /tmp/thresholds_before.json /tmp/thresholds_after.json && echo "BEFORE == AFTER"

echo "== 单元测试 =="
python3 -m unittest discover -s tests -v
