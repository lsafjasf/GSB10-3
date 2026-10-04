#!/bin/sh
# Reproduce all sample outputs in this directory.
set -u
cd "$(dirname "$0")/.."
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/release/bin" "$WORK/release/docs" "$WORK/release/assets"
printf '#!/bin/sh\necho release-1.0\n' > "$WORK/release/bin/run.sh"
printf 'release notes\n' > "$WORK/release/docs/NOTES.md"
printf 'ReadMe\n' > "$WORK/release/README.md"
head -c 1048576 /dev/urandom > "$WORK/release/assets/blob.bin"
ln -s run.sh "$WORK/release/bin/run"

M="$WORK/release/MANIFEST.jsonl"
python3 -m manifest_tool generate "$WORK/release" -o "$M"
python3 -m manifest_tool verify "$WORK/release" -m "$M" | tee examples/01_clean_verify.txt

printf 'release notes v2 (tampered)\n' > "$WORK/release/docs/NOTES.md"
rm "$WORK/release/README.md"
printf 'unexpected\n' > "$WORK/release/docs/EXTRA.txt"
python3 -m manifest_tool verify "$WORK/release" -m "$M" | tee examples/02_diff_report.txt
python3 -m manifest_tool verify "$WORK/release" -m "$M" --json > examples/06_diff_report.json

python3 -m manifest_tool selfcheck "$M" | tee examples/03_selfcheck_ok.txt

T="$WORK/release/MANIFEST.tampered.jsonl"
cp "$M" "$T"
python3 - "$T" <<'PY'
import json, sys
path = sys.argv[1]
lines = open(path).read().splitlines()
for i, line in enumerate(lines):
    obj = json.loads(line)
    if obj.get("kind") == "entry" and obj["path"] == "assets/blob.bin":
        obj["digest"] = "0" * 64
        lines[i] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
open(path, "w").write("\n".join(lines) + "\n")
PY
python3 -m manifest_tool selfcheck "$T" | tee examples/04_selfcheck_tampered.txt
python3 -m manifest_tool verify "$WORK/release" -m "$T" | tee examples/05_verify_tampered_manifest.txt

mkdir -p "$WORK/edge/empty_dir"
touch "$WORK/edge/File.txt" "$WORK/edge/FILE.txt"
ln -s File.txt "$WORK/edge/alias.txt"
head -c 3145728 /dev/urandom > "$WORK/edge/big.bin"
python3 -m manifest_tool generate "$WORK/edge" -o "$WORK/edge/MANIFEST.jsonl" \
    | tee examples/07_edge_cases_generate.txt
python3 -m manifest_tool verify "$WORK/edge" -m "$WORK/edge/MANIFEST.jsonl" \
    | tee examples/07_edge_cases_verify.txt
echo "demo done (exit codes 1/2 above are expected)"
