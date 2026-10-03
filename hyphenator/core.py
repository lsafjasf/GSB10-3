"""Multilingual hyphenation engine (Python standard library only).

Break rules live in external JSON tables (hyphenator/rules/<lang>.json)
and can be updated without touching this engine.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SOFT_HYPHEN = "\u00ad"  # U+00AD: used for insertion so restore() is lossless
RULES_DIR = Path(__file__).resolve().parent / "rules"
CORPUS_PATH = Path(__file__).resolve().parent / "corpus.json"

# German "Fugen-" linking elements allowed between compound parts.
FUGEN = ("ens", "es", "er", "en", "s", "n", "e")


class Hyphenator:
    """Rule-table driven hyphenator for one language."""

    def __init__(self, lang, rules_dir=None):
        self.lang = lang
        directory = Path(rules_dir) if rules_dir else RULES_DIR
        spec = json.loads((directory / (lang + ".json")).read_text(encoding="utf-8"))
        self.spec = spec
        self.min_prefix = int(spec.get("min_prefix", 2))
        self.min_suffix = int(spec.get("min_suffix", 2))
        self.mode = spec.get("mode", "pattern")
        self.rules = spec.get("rules", [])
        self.exceptions = {k.lower(): v for k, v in spec.get("exceptions", {}).items()}
        self.morphemes = sorted(
            (m.lower() for m in spec.get("morphemes", [])), key=len, reverse=True
        )
        self.no_break_before = set(spec.get("no_break_before", ""))
        self.no_break_after = set(spec.get("no_break_after", ""))
        self._compiled = {}
        # rule id -> set of words where the rule fired (coverage data)
        self.coverage = {}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def breakpoints(self, word):
        """Sorted break indices; index i means a break before word[i].

        Existing hyphens split the word into segments that are
        hyphenated independently.
        """
        if not word:
            return []
        breaks = []
        offset = 0
        for segment in word.split("-"):
            if segment:
                breaks.extend(b + offset for b in self._segment_breaks(segment))
            offset += len(segment) + 1
        return sorted(breaks)

    def hyphenate(self, word, hyphen=SOFT_HYPHEN):
        """Insert `hyphen` at every breakpoint. Round-trips via restore()
        when the default soft hyphen is used."""
        marks = set(self.breakpoints(word))
        out = []
        for i, ch in enumerate(word):
            if i in marks:
                out.append(hyphen)
            out.append(ch)
        return "".join(out)

    @staticmethod
    def restore(text):
        """Inverse of hyphenate(): strips soft hyphens, keeps real ones."""
        return text.replace(SOFT_HYPHEN, "")

    def validate(self, word, breaks=None):
        """Return min-prefix/suffix violations; an empty list means OK."""
        if breaks is None:
            breaks = self.breakpoints(word)
        segments = []
        start = 0
        for i, ch in enumerate(word):
            if ch == "-":
                segments.append((start, i))
                start = i + 1
        segments.append((start, len(word)))
        violations = []
        for b in breaks:
            owner = next(((s, e) for s, e in segments if s < b <= e), None)
            if owner is None:
                continue
            seg_start, seg_end = owner
            if b - seg_start < self.min_prefix:
                violations.append(
                    {"word": word, "break": b, "kind": "min_prefix",
                     "left": b - seg_start, "required": self.min_prefix}
                )
            if seg_end - b < self.min_suffix:
                violations.append(
                    {"word": word, "break": b, "kind": "min_suffix",
                     "right": seg_end - b, "required": self.min_suffix}
                )
        return violations

    def coverage_report(self):
        """rule id -> sorted list of words where the rule fired."""
        return {rid: sorted(ws) for rid, ws in sorted(self.coverage.items())}

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _record(self, rule_id, word):
        self.coverage.setdefault(rule_id, set()).add(word)

    def _segment_breaks(self, segment):
        lower = segment.lower()
        if lower in self.exceptions:
            return self._exception_breaks(lower, segment)
        if self.mode == "script":
            hits = self._script_breaks(segment)
        else:
            hits = self._pattern_breaks(lower, segment)
        n = len(segment)
        return sorted(b for b in hits if self.min_prefix <= b <= n - self.min_suffix)

    def _exception_breaks(self, lower, original):
        spec = self.exceptions[lower]
        breaks, pos = [], 0
        for ch in spec:
            if ch == "-":
                breaks.append(pos)
            else:
                pos += 1
        self._record("exceptions", original)
        return breaks

    def _pattern_breaks(self, lower, original):
        breaks = set()
        protected = set()
        for rule in self.rules:
            rtype = rule.get("type", "pattern")
            rid = rule["id"]
            if rtype == "pattern":
                hits = self._apply_pattern(rule, lower)
                if hits:
                    self._record(rid, original)
                    breaks |= hits
            elif rtype == "protect":
                spans = self._apply_protect(rule, lower)
                if spans:
                    self._record(rid, original)
                    protected |= spans
            elif rtype == "morpheme":
                hits = self._apply_morphemes(lower)
                if hits:
                    self._record(rid, original)
                    breaks |= hits
        return breaks - protected

    def _iter_matches(self, rule, lower):
        """Yield overlapping matches so adjacent patterns are all seen."""
        rid = rule["id"]
        if rid not in self._compiled:
            self._compiled[rid] = re.compile(rule["pattern"])
        rx = self._compiled[rid]
        pos = 0
        while pos <= len(lower):
            m = rx.search(lower, pos)
            if m is None:
                return
            yield m
            pos = m.start() + 1

    def _apply_pattern(self, rule, lower):
        hits = set()
        for m in self._iter_matches(rule, lower):
            if rule.get("break_after_match"):
                hits.add(m.end(0))
            if rule.get("break_before_match"):
                hits.add(m.start(0))
            if "break_after_group" in rule:
                hits.add(m.end(rule["break_after_group"]))
            if "break_before_group" in rule:
                hits.add(m.start(rule["break_before_group"]))
            for off in rule.get("breaks", []):
                hits.add(m.start(0) + off)
        return {h for h in hits if 0 < h < len(lower)}

    def _apply_protect(self, rule, lower):
        protected = set()
        for m in self._iter_matches(rule, lower):
            # internal boundaries of the match may not be broken
            protected.update(range(m.start(0) + 1, m.end(0)))
        return protected

    def _match_morpheme(self, lower, pos):
        for morph in self.morphemes:
            if lower.startswith(morph, pos):
                return morph
        return None

    def _apply_morphemes(self, lower):
        """Greedy longest-match compound segmentation (German)."""
        hits = set()
        n = len(lower)
        i = 0
        while i < n:
            morph = self._match_morpheme(lower, i)
            if morph is None:
                i += 1
                continue
            j = i + len(morph)
            if j >= n:
                break
            if self._match_morpheme(lower, j):
                hits.add(j)
                i = j
                continue
            for fug in FUGEN:  # linking element, break goes after it
                if lower.startswith(fug, j) and self._match_morpheme(lower, j + len(fug)):
                    hits.add(j + len(fug))
                    i = j + len(fug)
                    break
            else:
                i = j
        return hits

    def _script_breaks(self, segment):
        """CJK: break between characters, honouring kinsoku-style rules."""
        hits = []
        for i in range(1, len(segment)):
            prev, cur = segment[i - 1], segment[i]
            if cur in self.no_break_before or prev in self.no_break_after:
                continue
            hits.append(i)
        if hits:
            for rule in self.rules:
                if rule.get("type") == "script":
                    self._record(rule["id"], segment)
        return hits
