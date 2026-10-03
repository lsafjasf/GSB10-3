#!/bin/sh
# 运行全部自测（仅需 Python 3 标准库）
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
