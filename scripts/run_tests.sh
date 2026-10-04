#!/usr/bin/env bash
# 运行全部回归测试（只用 Python 标准库）。
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p 'test_*.py' -v
