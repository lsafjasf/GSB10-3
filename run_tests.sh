#!/usr/bin/env bash
# Run the full verification suite (stdlib only, Python 3.8+).
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/3 baseline (pre-refactor) tests =="
python3 -m unittest discover -s legacy_tests -v

echo "== 2/3 post-refactor suite: differential regression + boundaries + registry guards =="
python3 -m unittest discover -s tests -v

echo "== 3/3 threshold inventory =="
python3 -m tools.inventory
