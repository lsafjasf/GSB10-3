#!/usr/bin/env python3
"""Field-by-field differential test: socks5.parse_reply vs reference_parse.

Builds a corpus of reply buffers (hand-picked boundary cases plus
deterministic fuzz), runs both parsers on every case, and compares each
parsed field individually: ver, rep, rsv, atyp, addr_raw, addr, port --
or, for failing inputs, the error *kind* (truncated vs malformed).

Exit code 0 means the two implementations agree on every case.
"""

from __future__ import annotations

import random
import sys

import socks5
from reference_impl import RefError, reference_parse

FIELDS = ("ver", "rep", "rsv", "atyp", "addr_raw", "addr", "port")


def build_corpus():
    cases = []

    def add(name, data):
        cases.append((name, bytes(data)))

    # --- IPv4: every assigned REP code plus unassigned ones ---
    for rep in list(range(0x00, 0x09)) + [0x09, 0x7F, 0xFF]:
        add("ipv4-rep-%02x" % rep,
            [0x05, rep, 0x00, 0x01, 10, 0, 0, 1, 0x1F, 0x90])

    # --- IPv6 ---
    add("ipv6-ok", [0x05, 0x00, 0x00, 0x04] + list(range(16)) + [0x01, 0xBB])
    add("ipv6-all-zeros", [0x05, 0x00, 0x00, 0x04] + [0] * 16 + [0x00, 0x50])

    # --- domain: length boundaries 1, 2, 63, 254, 255 and invalid 0 ---
    for n in (1, 2, 63, 254, 255):
        add("domain-len-%d" % n,
            [0x05, 0x00, 0x00, 0x03, n] + [ord("a")] * n + [0x00, 0x50])
    add("domain-len-0", [0x05, 0x00, 0x00, 0x03, 0x00, 0x00, 0x50])
    # length octet claims 255 but the buffer ends early -> port must not be
    # read from the wrong offset
    add("domain-len-overrun",
        [0x05, 0x00, 0x00, 0x03, 0xFF] + [ord("a")] * 10 + [0x00, 0x50])

    # --- truncation: every proper prefix of a domain and an IPv6 reply ---
    full_dom = bytes([0x05, 0x00, 0x00, 0x03, 0x05]) + b"ab.cn" + bytes([0x1F, 0x90])
    for i in range(len(full_dom)):
        add("dom-trunc-%02d" % i, full_dom[:i])
    full_v6 = bytes([0x05, 0x00, 0x00, 0x04]) + bytes(range(16)) + bytes([0x00, 0x50])
    for i in range(len(full_v6)):
        add("ipv6-trunc-%02d" % i, full_v6[:i])
    full_v4 = bytes([0x05, 0x00, 0x00, 0x01, 127, 0, 0, 1, 0x00, 0x50])
    for i in range(len(full_v4)):
        add("ipv4-trunc-%02d" % i, full_v4[:i])

    # --- structural errors ---
    add("ipv4-trailing-garbage", list(full_v4) + [0xAA])
    add("bad-ver", [0x04, 0x00, 0x00, 0x01, 1, 2, 3, 4, 0x00, 0x50])
    add("bad-rsv", [0x05, 0x00, 0x01, 0x01, 1, 2, 3, 4, 0x00, 0x50])
    add("bad-atyp-02", [0x05, 0x00, 0x00, 0x02, 1, 2, 3, 4, 0x00, 0x50])
    add("bad-atyp-00", [0x05, 0x00, 0x00, 0x00, 1, 2, 3, 4, 0x00, 0x50])
    add("empty", [])

    # --- deterministic fuzz: pure random, and biased toward valid headers ---
    rng = random.Random(20261003)
    for i in range(300):
        add("fuzz-%03d" % i,
            [rng.randrange(256) for _ in range(rng.randrange(0, 40))])
    for i in range(300):
        buf = [0x05, rng.randrange(256), rng.choice([0x00, 0x00, 0x01]),
               rng.choice([0x01, 0x03, 0x04, 0x02, 0x00, 0xFF])]
        buf += [rng.randrange(256) for _ in range(rng.randrange(0, 30))]
        add("fuzz-hdr-%03d" % i, buf)
    return cases


def run_library(data):
    try:
        r = socks5.parse_reply(data)
    except socks5.TruncatedReply:
        return ("error", "truncated")
    except socks5.MalformedReply:
        return ("error", "malformed")
    return ("ok",) + tuple(getattr(r, f) for f in FIELDS)


def run_reference(data):
    try:
        r = reference_parse(data)
    except RefError as e:
        return ("error", e.kind)
    return ("ok",) + tuple(r[f] for f in FIELDS)


def main():
    cases = build_corpus()
    failures = 0
    ok_parse = ok_err = 0
    print("%-22s %-6s %s" % ("CASE", "RESULT", "DETAIL"))
    print("-" * 78)
    for name, data in cases:
        lib = run_library(data)
        ref = run_reference(data)
        if lib == ref:
            if lib[0] == "ok":
                ok_parse += 1
                detail = "fields match: " + " ".join(
                    "%s=%r" % (f, v) for f, v in zip(FIELDS, lib[1:]))
            else:
                ok_err += 1
                detail = "both reject as %s" % lib[1]
            print("%-22s %-6s %s" % (name, "OK", detail))
        else:
            failures += 1
            print("%-22s %-6s" % (name, "FAIL"))
            print("  library  : %r" % (lib,))
            print("  reference: %r" % (ref,))
    print("-" * 78)
    print("%d cases: %d parsed identically, %d rejected identically, %d MISMATCH"
          % (len(cases), ok_parse, ok_err, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
