"""Grapheme cluster segmentation per Unicode UAX #29 (Unicode 15.0.0).

Pure standard library. Break-property tables live in grapheme_data.py
(generated from the official UCD, see tools/gen_data.py).

Public API:
    segment(text)          -> list of grapheme clusters (forward)
    segment_reverse(text)  -> list of grapheme clusters (built from the end)
    boundaries(text)       -> sorted list of break offsets, incl. 0 and len
    is_boundary(text, i)   -> whether a break exists before offset i
    next_boundary(text, i) -> first break offset > i (<= len(text))
    prev_boundary(text, i) -> last break offset < i  (>= 0)
"""
from bisect import bisect_right

import grapheme_data as _d

# Grapheme_Cluster_Break property values, as small ints.
OTHER, CR, LF, CONTROL, EXTEND, ZWJ, RI, PREPEND, SPACINGMARK, L, V, T, LV, LVT = range(14)

_TABLES = {
    CR: _d.CR, LF: _d.LF, CONTROL: _d.CONTROL, EXTEND: _d.EXTEND,
    ZWJ: _d.ZWJ, RI: _d.REGIONAL_INDICATOR, PREPEND: _d.PREPEND,
    SPACINGMARK: _d.SPACINGMARK, L: _d.L, V: _d.V, T: _d.T,
    LV: _d.LV, LVT: _d.LVT,
}


def _flatten(ranges):
    starts, values = [], []
    for lo, hi in ranges:
        starts.append(lo)
        values.append(hi)
    return starts, values


# Per-property lookup structures: (sorted start offsets, matching end offsets).
_LOOKUP = {prop: _flatten(ranges) for prop, ranges in _TABLES.items()}
_EXTPICT = _flatten(_d.EXTENDED_PICTOGRAPHIC)


def _in_ranges(cp, table):
    starts, ends = table
    i = bisect_right(starts, cp) - 1
    return i >= 0 and cp <= ends[i]


def _gcb(ch):
    """Grapheme_Cluster_Break property of a single character."""
    cp = ord(ch)
    for prop, table in _LOOKUP.items():
        if _in_ranges(cp, table):
            return prop
    return OTHER


def _is_extpict(ch):
    return _in_ranges(ord(ch), _EXTPICT)


def is_boundary(text, i):
    """True if there is a grapheme cluster break before offset i.

    Offsets 0 and len(text) are always boundaries. Implements UAX #29
    rules GB1-GB999 for extended grapheme clusters.
    """
    if i <= 0 or i >= len(text):
        return True
    left = _gcb(text[i - 1])
    right = _gcb(text[i])

    # GB3: CR x LF
    if left == CR and right == LF:
        return False
    # GB4 / GB5: break around controls (incl. bidi controls, ZWNJ excluded:
    # ZWNJ has GCB=Extend and is handled by GB9).
    if left in (CR, LF, CONTROL) or right in (CR, LF, CONTROL):
        return True
    # GB6-GB8: Hangul syllable sequences.
    if left == L and right in (L, V, LV, LVT):
        return False
    if left in (LV, V) and right in (V, T):
        return False
    if left in (LVT, T) and right == T:
        return False
    # GB9: x (Extend | ZWJ)  -- combining marks, variation selectors,
    # emoji skin-tone modifiers, ZWNJ all glue to the left.
    if right in (EXTEND, ZWJ):
        return False
    # GB9a: x SpacingMark
    if right == SPACINGMARK:
        return False
    # GB9b: Prepend x
    if left == PREPEND:
        return False
    # GB11: Extended_Pictographic Extend* ZWJ x Extended_Pictographic
    if left == ZWJ and _is_extpict(text[i]):
        j = i - 2
        while j >= 0 and _gcb(text[j]) == EXTEND:
            j -= 1
        if j >= 0 and _is_extpict(text[j]):
            return False
    # GB12 / GB13: break between regional indicators only before an even
    # run of them (flags form pairs).
    if left == RI and right == RI:
        run = 0
        j = i - 1
        while j >= 0 and _gcb(text[j]) == RI:
            run += 1
            j -= 1
        if run % 2 == 1:
            return False
    # GB999: otherwise break.
    return True


def boundaries(text):
    """All cluster break offsets in text, including 0 and len(text)."""
    return [0] + [i for i in range(1, len(text)) if is_boundary(text, i)] + (
        [len(text)] if text else [])


def segment(text):
    """Split text into grapheme clusters, left to right."""
    b = boundaries(text)
    return [text[a:c] for a, c in zip(b, b[1:])]


def next_boundary(text, i):
    """First cluster boundary strictly after offset i."""
    if i < 0:
        i = 0
    j = i + 1
    while j < len(text) and not is_boundary(text, j):
        j += 1
    return j


def _is_safe_anchor(text, k):
    """True if offset k is a break no matter what precedes it in text."""
    left = _gcb(text[k - 1])
    right = _gcb(text[k])
    if left == CR and right == LF:            # GB3 joins across k
        return False
    if left in (CR, LF, CONTROL) or right in (CR, LF, CONTROL):
        return True                            # GB4 / GB5 are unconditional
    if left == PREPEND:                        # GB9b may join across k
        return False
    if right in (EXTEND, ZWJ, SPACINGMARK):    # GB9 / GB9a join from the left
        return False
    if right == RI:                            # GB12/13 depend on run parity
        return False
    if right in (L, V, T, LV, LVT):            # GB6-8 join from the left
        return False
    if left == ZWJ and _is_extpict(text[k]):   # GB11 needs deeper context
        return False
    return True


def prev_boundary(text, i):
    """Last cluster boundary strictly before offset i.

    Scans back to a "safe anchor" (an offset that is a break regardless of
    earlier context), then re-segments forward from there. The scan is
    short in practice: anchors occur at every plain base character.
    """
    if i > len(text):
        i = len(text)
    if i <= 1:
        return 0
    k = i - 1
    while k > 0 and not _is_safe_anchor(text, k):
        k -= 1
    # k is a true boundary of the full text; segment the tail forward.
    b = boundaries(text[k:i])
    return k + (b[-2] if len(b) > 1 else 0)


def segment_reverse(text):
    """Split text into clusters by repeatedly finding the previous boundary.

    Guaranteed to agree with segment(); the test suite asserts this on
    randomized inputs.
    """
    out = []
    pos = len(text)
    while pos > 0:
        p = prev_boundary(text, pos)
        out.append(text[p:pos])
        pos = p
    out.reverse()
    return out
