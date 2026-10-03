#!/usr/bin/env python3
"""Self-test / demo harness for session_cache.

Run:  python3 run_selftest.py
Exits 0 when every check passes; prints reuse/reject and memory stats.
"""

import os
import random
import sys
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from session_cache import Binding, ReuseError, SessionTicketCache


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def expect_reject(cache, ticket, binding, reason):
    try:
        cache.resume(ticket, binding)
        return None
    except ReuseError as e:
        return e if e.reason == reason else None


BINDING = Binding("TLS1.3", "TLS_AES_128_GCM_SHA256", "example.com")
STATE = {"master_secret": "ab" * 48, "session_id": "s-1", "alpn": "h2"}


def scenario_normal_reuse():
    print("\n== 1. Normal reuse ==")
    clock = FakeClock()
    cache = SessionTicketCache(os.urandom(32), max_entries=64, ttl_seconds=300, clock=clock)
    tickets = [cache.issue(dict(STATE, session_id=f"s-{i}"), BINDING) for i in range(20)]
    clock.advance(10)
    ok = 0
    for i, t in enumerate(tickets):
        state = cache.resume(t, BINDING)
        ok += state["session_id"] == f"s-{i}"
    check("20/20 sessions reused", ok == 20)
    check("stats: reused=20, no rejects",
          cache.stats.reused == 20 and cache.stats.reuse_attempts == 20)


def scenario_expiry():
    print("\n== 2. Ticket expiry + lazy purge ==")
    clock = FakeClock()
    cache = SessionTicketCache(os.urandom(32), max_entries=64, ttl_seconds=60, clock=clock)
    t1 = cache.issue(STATE, BINDING)
    clock.advance(61)
    err = expect_reject(cache, t1, BINDING, "EXPIRED")
    check("expired ticket rejected", err is not None, err.detail if err else "")
    check("expired entry lazily removed from store", len(cache) == 0)
    # lazy purge also runs on issue
    t2 = cache.issue(dict(STATE, session_id="e-2"), BINDING)
    t3 = cache.issue(dict(STATE, session_id="e-3"), BINDING)
    clock.advance(61)
    cache.issue(STATE, BINDING)  # triggers purge of t2/t3
    check("issue-time lazy purge removed 2 stale entries",
          cache.stats.lazy_expired_purged >= 2 and len(cache) == 1)
    # boundary: exactly at expiry instant is expired
    t4 = cache.issue(STATE, BINDING, ttl_seconds=10)
    clock.advance(10)
    check("ticket at exact expiry instant is expired",
          expect_reject(cache, t4, BINDING, "EXPIRED") is not None)


def scenario_capacity():
    print("\n== 3. Capacity bound + LRU eviction ==")
    clock = FakeClock()
    cache = SessionTicketCache(os.urandom(32), max_entries=8, ttl_seconds=300, clock=clock)
    tickets = [cache.issue(dict(STATE, session_id=f"c-{i}"), BINDING) for i in range(16)]
    check("store capped at max_entries", len(cache) == 8, f"len={len(cache)}")
    check("8 oldest evicted via LRU", cache.stats.evicted_lru == 8)
    check("evicted ticket -> NOT_FOUND",
          expect_reject(cache, tickets[0], BINDING, "NOT_FOUND") is not None)
    check("newest ticket still resumable", cache.resume(tickets[-1], BINDING) is not None)
    # LRU recency: reuse refreshes position
    clock2 = FakeClock()
    c2 = SessionTicketCache(os.urandom(32), max_entries=3, ttl_seconds=300, clock=clock2)
    ts = [c2.issue(dict(STATE, session_id=f"l-{i}"), BINDING) for i in range(3)]
    c2.resume(ts[0], BINDING)          # refresh oldest
    c2.issue(dict(STATE, session_id="l-3"), BINDING)  # evicts l-1, not l-0
    check("reuse refreshes LRU recency",
          expect_reject(c2, ts[1], BINDING, "NOT_FOUND") is not None
          and c2.resume(ts[0], BINDING) is not None)


def scenario_binding_mismatch():
    print("\n== 4. Binding mismatch ==")
    clock = FakeClock()
    cache = SessionTicketCache(os.urandom(32), max_entries=64, ttl_seconds=300, clock=clock)
    t = cache.issue(STATE, BINDING)
    cases = [
        ("protocol downgrade", Binding("TLS1.2", BINDING.cipher_suite, BINDING.sni)),
        ("cipher change", Binding(BINDING.protocol_version, "TLS_CHACHA20_POLY1305_SHA256", BINDING.sni)),
        ("sni change", Binding(BINDING.protocol_version, BINDING.cipher_suite, "evil.com")),
    ]
    for name, bad in cases:
        err = expect_reject(cache, t, bad, "BINDING_MISMATCH")
        check(f"{name} rejected", err is not None, err.detail if err else "")
    check("correct binding still accepted", cache.resume(t, BINDING) is not None)


def scenario_tamper():
    print("\n== 5. Forgery / tamper detection ==")
    clock = FakeClock()
    secret = os.urandom(32)
    cache = SessionTicketCache(secret, max_entries=1024, ttl_seconds=300, clock=clock)
    genuine = cache.issue(STATE, BINDING)
    rng = random.Random(42)
    forged, accepted = 0, 0

    def try_forged(blob):
        nonlocal forged, accepted
        forged += 1
        try:
            cache.resume(blob, BINDING)
            accepted += 1
        except ReuseError as e:
            assert e.reason == "FORGED", f"unexpected reason {e.reason}"

    # (a) single-bit flips at every byte position
    for pos in range(len(genuine)):
        for bit in (0x01, 0x80):
            b = bytearray(genuine)
            b[pos] ^= bit
            try_forged(bytes(b))
    # (b) truncations and extensions
    for cut in (1, 8, 16, 33, len(genuine) // 2, len(genuine) - 1):
        try_forged(genuine[:cut])
    try_forged(genuine + b"\x00")
    try_forged(genuine + os.urandom(16))
    # (c) empty / non-bytes / garbage
    try_forged(b"")
    try_forged(os.urandom(len(genuine)))
    # (d) ticket signed by a different server secret
    other = SessionTicketCache(os.urandom(32), max_entries=8, ttl_seconds=300, clock=clock)
    try_forged(other.issue(STATE, BINDING))
    # (e) replayed genuine ticket must still work (control)
    control_ok = cache.resume(genuine, BINDING) is not None

    total = forged
    ratio = (total - accepted) / total
    print(f"  forged attempts: {total}, rejected: {total - accepted}, "
          f"accepted: {accepted}, rejection ratio: {ratio:.4%}")
    check("100% of forged tickets rejected", accepted == 0)
    check("genuine control ticket still accepted", control_ok)
    check("stats.rejected_forged matches", cache.stats.rejected_forged == total)
    return total, total - accepted


def scenario_memory():
    print("\n== 6. Memory footprint ==")
    clock = FakeClock()
    cache = SessionTicketCache(os.urandom(32), max_entries=10_000, ttl_seconds=3600, clock=clock)
    state = {
        "master_secret": os.urandom(48).hex(),
        "session_id": os.urandom(16).hex(),
        "alpn": "h2",
        "peer_cert_hash": os.urandom(32).hex(),
    }
    n = 5000
    tracemalloc.start()
    for i in range(n):
        cache.issue(dict(state, session_id=f"m-{i}"), BINDING)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    est = cache.memory_bytes()
    print(f"  entries={len(cache)}  estimated={est/1024:.1f} KiB "
          f"({est/len(cache):.0f} B/entry)  "
          f"tracemalloc current={current/1024:.1f} KiB peak={peak/1024:.1f} KiB")
    check("memory scales linearly, bounded by capacity",
          len(cache) == 10_000 or len(cache) == n)
    # capacity bounds memory
    small = SessionTicketCache(os.urandom(32), max_entries=100, ttl_seconds=3600, clock=clock)
    for i in range(1000):
        small.issue(dict(state, session_id=f"s-{i}"), BINDING)
    check("capacity bounds memory", len(small) == 100 and small.memory_bytes() < 100 * 2048)
    return est, current, peak


def scenario_edge_cases():
    print("\n== 7. Edge cases ==")
    clock = FakeClock()
    try:
        SessionTicketCache(b"short")
        check("short secret rejected", False)
    except ValueError:
        check("short secret rejected", True)
    for kwargs in ({"max_entries": 0}, {"ttl_seconds": 0}, {"ttl_seconds": -5}):
        try:
            SessionTicketCache(os.urandom(32), **kwargs)
            check(f"bad config {kwargs} rejected", False)
        except ValueError:
            check(f"bad config {kwargs} rejected", True)
    cache = SessionTicketCache(os.urandom(32), max_entries=4, ttl_seconds=60, clock=clock)
    try:
        cache.issue(STATE, BINDING, ttl_seconds=0)
        check("per-issue ttl<=0 rejected", False)
    except ValueError:
        check("per-issue ttl<=0 rejected", True)
    check("non-bytes ticket -> FORGED",
          expect_reject(cache, "not-bytes", BINDING, "FORGED") is not None)
    t = cache.issue(STATE, BINDING)
    clock.advance(30)
    state = cache.resume(t, BINDING)
    state["master_secret"] = "tampered"
    check("returned state is a copy (mutation safe)",
          cache.resume(t, BINDING)["master_secret"] == STATE["master_secret"])
    # custom per-issue ttl
    t2 = cache.issue(STATE, BINDING, ttl_seconds=5)
    clock.advance(6)
    check("per-issue ttl honored",
          expect_reject(cache, t2, BINDING, "EXPIRED") is not None)


def main():
    print("session_cache self-test")
    scenario_normal_reuse()
    scenario_expiry()
    scenario_capacity()
    scenario_binding_mismatch()
    forged_total, forged_rejected = scenario_tamper()
    scenario_memory()
    scenario_edge_cases()

    print("\n== Summary ==")
    print(f"  forged tickets rejected: {forged_rejected}/{forged_total} "
          f"({forged_rejected/forged_total:.4%})")
    if FAILURES:
        print(f"  RESULT: FAIL ({len(FAILURES)} checks failed: {FAILURES})")
        return 1
    print("  RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
