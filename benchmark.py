"""Benchmark / data-collection script for the session-state cache.

Produces:
  * reuse vs. rejection counts for a realistic mixed workload
  * forged-ticket rejection ratio over N random tamper attempts
  * memory footprint per cached entry (sys.getsizeof + tracemalloc)

Run:  python3 benchmark.py
"""

import base64
import json
import random
import secrets
import sys
import tracemalloc

from session_cache import RejectReason, SessionCache


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


def deep_size(obj, seen=None):
    """Recursively measure an object's memory footprint in bytes."""
    seen = seen or set()
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(deep_size(k, seen) + deep_size(v, seen)
                    for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(deep_size(i, seen) for i in obj)
    elif hasattr(obj, "__dict__"):
        size += deep_size(vars(obj), seen)
    elif hasattr(obj, "__slots__"):
        size += sum(deep_size(getattr(obj, s), seen)
                    for s in obj.__slots__ if hasattr(obj, s))
    return size


def section(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def scenario_mixed_workload():
    section("1. Mixed workload: reuse / expiry / capacity / binding change")
    clock = FakeClock()
    cache = SessionCache(secrets.token_bytes(32), max_entries=100,
                         ttl_seconds=300.0, clock=clock)
    rng = random.Random(42)
    tickets = {}

    # 150 handshakes into a 100-slot cache -> 50 LRU evictions.
    for i in range(150):
        tickets[f"s{i}"] = cache.store(
            f"s{i}", "TLS1.3", "TLS_AES_256_GCM_SHA384",
            secret=secrets.token_bytes(48))

    reused = rejected = 0
    reasons = {}
    for i in range(150):
        sid = f"s{i}"
        ticket = tickets[sid]
        roll = rng.random()
        if roll < 0.05:
            ver, cs = "TLS1.2", "TLS_AES_256_GCM_SHA384"      # ver change
        elif roll < 0.10:
            ver, cs = "TLS1.3", "TLS_CHACHA20_POLY1305_SHA256"  # cs change
        else:
            ver, cs = "TLS1.3", "TLS_AES_256_GCM_SHA384"
        result = cache.resume(ticket, ver, cs)
        if result.reused:
            reused += 1
        else:
            rejected += 1
            reasons[result.reason.value] = \
                reasons.get(result.reason.value, 0) + 1

    # Let half the TTL pass, store fresh sessions, then try old tickets.
    clock.advance(301.0)
    stale = cache.resume(tickets["s149"], "TLS1.3",
                         "TLS_AES_256_GCM_SHA384")
    assert stale.reason is RejectReason.EXPIRED_TICKET
    reasons[stale.reason.value] = reasons.get(stale.reason.value, 0) + 1
    rejected += 1

    print(f"  handshakes issued      : {cache.stats.issued}")
    print(f"  cache capacity         : {cache.max_entries}")
    print(f"  live entries           : {cache.live_size()}")
    print(f"  LRU evictions          : {cache.stats.evictions}")
    print(f"  lazy expiries          : {cache.stats.lazy_expiries}")
    print(f"  resumes accepted       : {reused}")
    print(f"  resumes rejected       : {rejected}")
    for reason, count in sorted(reasons.items()):
        print(f"    - {reason:<28} {count}")


def scenario_forgery():
    section("2. Forgery resistance: random tamper attempts")
    clock = FakeClock()
    cache = SessionCache(secrets.token_bytes(32), max_entries=64,
                         ttl_seconds=300.0, clock=clock)
    rng = random.Random(7)
    tickets = [cache.store(f"s{i}", "TLS1.3", "TLS_AES_256_GCM_SHA384")
               for i in range(20)]

    def flip_byte(raw: bytes) -> bytes:
        i = rng.randrange(len(raw))
        return raw[:i] + bytes([raw[i] ^ (1 << rng.randrange(8))]) + raw[i+1:]

    def tamper(ticket):
        mode = rng.randrange(4)
        payload_b64, mac_b64 = ticket.split(".")
        pad = "=" * (-len(payload_b64) % 4)
        raw_payload = base64.urlsafe_b64decode(payload_b64 + pad)
        raw_mac = base64.urlsafe_b64decode(mac_b64 +
                                           "=" * (-len(mac_b64) % 4))
        if mode == 0:  # bit flip at byte level (payload or MAC)
            if rng.random() < 0.5:
                raw_payload = flip_byte(raw_payload)
            else:
                raw_mac = flip_byte(raw_mac)
            new_b64 = base64.urlsafe_b64encode(
                raw_payload).rstrip(b"=").decode()
            mac_out = base64.urlsafe_b64encode(
                raw_mac).rstrip(b"=").decode()
            return new_b64 + "." + mac_out
        payload = json.loads(raw_payload.decode())
        if mode == 1:
            payload["ver"] = "TLS1.2"
        elif mode == 2:
            payload["cs"] = "TLS_CHACHA20_POLY1305_SHA256"
        else:
            payload["exp"] = payload["exp"] + rng.randrange(1, 10**6)
        raw = json.dumps(payload, separators=(",", ":"),
                         sort_keys=True).encode()
        new_b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        if rng.random() < 0.5:
            return new_b64 + "." + mac_b64            # keep stale MAC
        fake_mac = base64.urlsafe_b64encode(
            secrets.token_bytes(32)).rstrip(b"=").decode()
        return new_b64 + "." + fake_mac               # attach random MAC

    attempts = 5000
    accepted = 0
    for _ in range(attempts):
        forged = tamper(rng.choice(tickets))
        if cache.resume(forged, "TLS1.3", "TLS_AES_256_GCM_SHA384").reused:
            accepted += 1
    rejected = attempts - accepted
    print(f"  forged attempts        : {attempts}")
    print(f"  accepted (should be 0) : {accepted}")
    print(f"  rejected               : {rejected}")
    print(f"  forged rejection ratio : {rejected / attempts:.4%}")


def scenario_memory():
    section("3. Memory footprint")
    for n in (1, 100, 1_000, 10_000):
        clock = FakeClock()
        cache = SessionCache(secrets.token_bytes(32), max_entries=n,
                             ttl_seconds=3600.0, clock=clock)
        tracemalloc.start()
        base = tracemalloc.get_traced_memory()[0]
        for i in range(n):
            cache.store(f"session-{i:06d}", "TLS1.3",
                        "TLS_AES_256_GCM_SHA384",
                        secret=secrets.token_bytes(48))
        current, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        delta = current - base
        print(f"  entries={n:>6}  total={delta / 1024:>10.1f} KiB  "
              f"per-entry={delta / n:>7.1f} B")

    clock = FakeClock()
    cache = SessionCache(secrets.token_bytes(32), max_entries=1,
                         ttl_seconds=3600.0, clock=clock)
    cache.store("s", "TLS1.3", "TLS_AES_256_GCM_SHA384",
                secret=b"x" * 48)
    state = next(iter(cache._store.values()))
    print(f"  deep_size(SessionState)      : {deep_size(state)} B")
    print(f"  deep_size(cache, 1 entry)    : {deep_size(cache)} B")


if __name__ == "__main__":
    scenario_mixed_workload()
    scenario_forgery()
    scenario_memory()
