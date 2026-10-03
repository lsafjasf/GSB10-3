#!/usr/bin/env python3
"""Generate grapheme_data.py from official Unicode data files (UCD 15.0.0).

Usage:
    python3 tools/gen_data.py GraphemeBreakProperty.txt emoji-data.txt > grapheme_data.py

Sources:
    https://www.unicode.org/Public/15.0.0/ucd/auxiliary/GraphemeBreakProperty.txt
    https://www.unicode.org/Public/15.0.0/ucd/emoji/emoji-data.txt
"""
import sys


def parse(path, wanted):
    """Return {value: [(lo, hi), ...]} for lines whose property is in `wanted`."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            rng, prop = (part.strip() for part in line.split(";"))
            if prop not in wanted:
                continue
            if ".." in rng:
                lo, hi = (int(x, 16) for x in rng.split(".."))
            else:
                lo = hi = int(rng, 16)
            out.setdefault(prop, []).append((lo, hi))
    return out


def fmt(name, ranges):
    lines = [f"{name} = ("]
    for lo, hi in ranges:
        lines.append(f"    (0x{lo:04X}, 0x{hi:04X}),")
    lines.append(")")
    return "\n".join(lines)


def main():
    gcb = parse(sys.argv[1], {"Prepend", "CR", "LF", "Control", "Extend",
                              "ZWJ", "Regional_Indicator", "SpacingMark",
                              "L", "V", "T", "LV", "LVT"})
    ext = parse(sys.argv[2], {"Extended_Pictographic"})["Extended_Pictographic"]
    print('"""Grapheme cluster break tables, generated from UCD 15.0.0.')
    print("Do not edit by hand; see tools/gen_data.py.")
    print('"""')
    print()
    for prop in ("CR", "LF", "Control", "Extend", "ZWJ", "Regional_Indicator",
                 "Prepend", "SpacingMark", "L", "V", "T", "LV", "LVT"):
        print(fmt(prop.upper(), gcb.get(prop, [])))
        print()
    print(fmt("EXTENDED_PICTOGRAPHIC", ext))


if __name__ == "__main__":
    main()
