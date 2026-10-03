"""Multilingual hyphenation / line-break engine.

Two rule families, selected by ``"type"`` in the external JSON rule table:

* ``"pattern"`` -- Franklin M. Liang's TeX-style hyphenation patterns.
  Odd numbered levels permit a break, even numbered levels forbid it
  (a higher level wins). German additionally splits compounds along a
  configurable morpheme list before applying the patterns.
* ````cjk"`` -- per-character break opportunities governed by kinsoku
  rules (characters that must not start / end a line).

Only the Python standard library is used.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent / "rules"

SOFT_HYPHEN = "\u00ad"

# Latin letters including the common Latin-1 supplement range; hyphenated
# components inside one word are kept together as a single match.
_LATIN_RUN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:-[A-Za-zÀ-ÖØ-öø-ÿ]+)*")


def _parse_pattern(pattern: str):
    """Parse a TeX-style pattern, e.g. ``"hy3p"`` -> ("hyp", [0,0,3,0,0])."""
    levels = [0]
    letters = []
    for ch in pattern:
        if ch.isdigit():
            levels[-1] = int(ch)
        else:
            letters.append(ch)
            levels.append(0)
    return "".join(letters), levels


class RuleSet:
    """One external language rule table (patterns, morphemes, kinsoku)."""

    def __init__(self, lang: str, rules_dir: str | Path | None = None):
        base = Path(rules_dir) if rules_dir is not None else RULES_DIR
        self.path = base / f"{lang}.json"
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.lang = lang
        self.name = data.get("language", lang)
        self.kind = data.get("type", "pattern")
        self.min_left = int(data.get("min_left", 2))
        self.min_right = int(data.get("min_right", 2))
        self.patterns = [_parse_pattern(p) for p in data.get("patterns", [])]
        self.morphemes = sorted(
            (m.lower() for m in data.get("morphemes", [])), key=len, reverse=True
        )
        self._morpheme_set = set(self.morphemes)
        self.no_break_before = set(data.get("no_break_before", []))
        self.no_break_after = set(data.get("no_break_after", []))
        self.cases = data.get("cases", [])

    def to_meta(self) -> dict:
        return {
            "language": self.name,
            "type": self.kind,
            "min_left": self.min_left,
            "min_right": self.min_right,
            "patterns": len(self.patterns),
            "morphemes": len(self.morphemes),
            "kinsoku": len(self.no_break_before) + len(self.no_break_after),
        }


class Hyphenator:
    """Compute break points and insert / remove soft hyphens."""

    def __init__(self, lang: str, rules_dir: str | Path | None = None):
        self.rules = RuleSet(lang, rules_dir)

    # ------------------------------------------------------------------ API

    def break_points(self, text: str) -> list[int]:
        """Return sorted break positions.

        A break position ``p`` means a break is allowed between
        ``text[p-1]`` and ``text[p]`` (``1 <= p <= len(text)-1``).
        """
        if self.rules.kind == "cjk":
            return sorted(self._cjk_breaks(text))
        return self._latin_breaks(text)

    def hyphenate(self, text: str, hyphen: str = SOFT_HYPHEN) -> str:
        """Insert *hyphen* at every allowed break point."""
        return insert_hyphens(text, self.break_points(text), hyphen)

    @staticmethod
    def dehyphenate(text: str, hyphen: str = SOFT_HYPHEN) -> str:
        return remove_hyphens(text, hyphen)

    # -------------------------------------------------------------- helpers

    def _latin_breaks(self, text: str) -> list[int]:
        points: set[int] = set()
        for match in _LATIN_RUN.finditer(text):
            segment = match.group(0)
            offset = match.start()
            parts = segment.split("-")
            cursor = 0
            for index, part in enumerate(parts):
                if index > 0 and cursor > 0:
                    # Existing hard hyphen: break immediately after it.
                    # Explicit hyphens are exempt from the min-length rule.
                    points.add(offset + cursor)
                if part:
                    candidate = set(self._morpheme_breaks(part))
                    candidate.update(self._pattern_breaks(part))
                    candidate = {
                        p
                        for p in candidate
                        if self.rules.min_left <= p <= len(part) - self.rules.min_right
                    }
                    points.update(offset + cursor + p for p in candidate)
                cursor += len(part) + 1  # +1 for the consumed '-'
        return sorted(points)

    def _pattern_breaks(self, word: str) -> set[int]:
        """Liang algorithm on a single hyphen-free component."""
        levels = [0] * (len(word) + 3)
        dotted = "." + word.lower() + "."
        for letters, pattern_levels in self.rules.patterns:
            start = 0
            while True:
                found = dotted.find(letters, start)
                if found < 0:
                    break
                for slot, value in enumerate(pattern_levels):
                    if value and value > levels[found + slot]:
                        levels[found + slot] = value
                start = found + 1
        # Gap before word[p] (0-based) is the break after p characters;
        # in the dotted word that slot is `p + 1`.
        return {
            p for p in range(1, len(word)) if levels[p + 1] % 2 == 1
        }

    def _morpheme_breaks(self, word: str) -> set[int]:
        """Segment a Germanic compound into known morphemes (best coverage).

        Dynamic program: minimise uncovered characters, then the number of
        segments. Every boundary between two known morphemes is a candidate
        break point (still subject to min prefix/suffix filtering later).
        """
        lowered = word.lower()
        n = len(lowered)
        dp: list[tuple[int, int] | None] = [None] * (n + 1)
        dp[0] = (0, 0)
        previous = [0] * (n + 1)
        for end in range(1, n + 1):
            best = None
            best_start = 0
            for start in range(0, end):
                if dp[start] is None:
                    continue
                uncovered, segments = dp[start]
                piece = lowered[start:end]
                if piece in self.rules._morpheme_set:
                    candidate = (uncovered, segments + 1)
                else:
                    candidate = (uncovered + (end - start), segments + 1)
                if best is None or candidate < best:
                    best = candidate
                    best_start = start
            dp[end] = best
            previous[end] = best_start

        breaks: set[int] = set()
        end = n
        while end > 0:
            start = previous[end]
            if (
                0 < start < n
                and lowered[start:end] in self.rules._morpheme_set
                and lowered[previous[start]:start] in self.rules._morpheme_set
            ):
                breaks.add(start)
            end = start
        return breaks

    def _cjk_breaks(self, text: str) -> set[int]:
        points: set[int] = set()
        for pos in range(1, len(text)):
            before, after = text[pos - 1], text[pos]
            if after in self.rules.no_break_before:
                continue
            if before in self.rules.no_break_after:
                continue
            # Keep adjacent Latin letters/digits together; they would be
            # hyphenated by a Latin rule table in a mixed-text pipeline.
            if (
                before.isascii()
                and after.isascii()
                and before.isalnum()
                and after.isalnum()
            ):
                continue
            if pos < self.rules.min_left or len(text) - pos < self.rules.min_right:
                continue
            points.add(pos)
        return points


def insert_hyphens(text: str, points, hyphen: str = SOFT_HYPHEN) -> str:
    """Insert *hyphen* before ``text[p]`` for each break position ``p``."""
    positions = set(points)
    return "".join(
        (hyphen if i in positions else "") + ch for i, ch in enumerate(text)
    )


def remove_hyphens(text: str, hyphen: str = SOFT_HYPHEN) -> str:
    """Inverse of :func:`insert_hyphens`."""
    return text.replace(hyphen, "")
