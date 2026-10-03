# Multilingual Hyphenation (stdlib only)

Language-aware word breaking: syllable/pattern hyphenation for English,
compound-aware hyphenation for German, and kinsoku (禁則) line-breaking
for Chinese / Japanese / Korean. Python 3 standard library only.

## Layout

- `hyphenation/core.py` — engine: Liang patterns (Latin), morpheme
  compound splitting (German), kinsoku rules (CJK).
- `hyphenation/rules/{en,de,zh,ja,ko}.json` — external, editable rule
  tables: patterns, morphemes, kinsoku sets, min prefix/suffix lengths,
  and the per-language test cases.
- `hyphenation/selftest.py` — rule-case, constraint, round-trip and
  edge-case tests (`unittest`).
- `hyphenation/coverage.py` — rule coverage report -> `coverage.json`.
- `hyphenation/coverage.json` — generated coverage data (26/26 cases).

## Rules are external and updatable

Each `rules/<lang>.json` holds:

| key | meaning |
| --- | --- |
| `type` | `pattern` (Latin) or `cjk` (kinsoku) |
| `min_left` / `min_right` | min chars before/after a break point |
| `patterns` | TeX-style Liang patterns, e.g. `hy3p` (odd=break, even=forbid) |
| `morphemes` | compound parts (German), boundaries become break candidates |
| `no_break_before` / `no_break_after` | kinsoku sets (CJK) |
| `cases` | regression cases with expected break positions |

Edit a table and rerun the tests — no code changes needed.

## Run

```sh
cd <repo root>
python3 -m hyphenation.selftest     # 8 tests: cases, constraints, round-trip, edge cases
python3 -m hyphenation.coverage     # per-language coverage counts -> coverage.json
python3 -m hyphenation de Donaudampfschifffahrtsgesellschaftskapitän
python3 -m hyphenation ja 私は日本語を勉強しています。
```

## Guarantees checked by the tests

- **Min prefix/suffix**: every break point satisfies `min_left`/`min_right`
  (hard hyphens already in the text are exempt); the suite asserts the
  violation count is zero over all cases plus an extra corpus.
- **Round-trip**: `remove_hyphens(hyphenate(text)) == text` for every
  case; soft hyphen `U+00AD` is the default marker.
- **Edge cases**: monosyllabic words (no breaks), very long German
  compounds (morpheme boundaries present), hyphenated words (break after
  existing hyphens, components hyphenated independently), non-Latin
  scripts (no break before `。`/small kana, no break after opening
  brackets).

## API

```python
from hyphenation import Hyphenator
h = Hyphenator("en")
h.break_points("hyphenation")   # [2, 6, 7]
h.hyphenate("hyphenation")      # 'hy\xadphen\xada\xadtion'
h.dehyphenate(h.hyphenate("hyphenation"))  # 'hyphenation'
```
