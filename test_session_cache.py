"""Self-tests for the session-state cache (stdlib unittest only)."""

import base64
import json
import secrets
import unittest

from session_cache import (
    RejectReason,
    ResumeStatus,
    SessionCache,
)
from session_cache.ticket import TicketError, issue_ticket, verify_ticket


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_cache(max_entries=4, ttl=60.0, clock=None):
    clock = clock or FakeClock()
    key = secrets.token_bytes(32)
    return SessionCache(key, max_entries=max_entries,
                        ttl_seconds=ttl, clock=clock), clock, key


class TestNormalReuse(unittest.TestCase):
    def test_store_and_resume(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384",
                             secret=b"master-secret")
        result = cache.resume(ticket, "TLS1.3", "TLS_AES_256_GCM_SHA384")
        self.assertEqual(result.status, ResumeStatus.REUSED)
        self.assertTrue(result.reused)
        self.assertEqual(result.state.session_id, "s1")
        self.assertEqual(result.state.secret, b"master-secret")
        self.assertEqual(cache.stats.reused, 1)
        self.assertEqual(cache.stats.rejected, 0)

    def test_repeated_resume_of_same_ticket(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        for _ in range(3):
            self.assertTrue(
                cache.resume(ticket, "TLS1.3",
                             "TLS_AES_256_GCM_SHA384").reused)
        self.assertEqual(cache.stats.reused, 3)

    def test_reissue_same_id_replaces_state(self):
        cache, clock, _ = make_cache()
        cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        clock.advance(10)
        ticket2 = cache.store("s1", "TLS1.3", "TLS_CHACHA20_POLY1305_SHA256")
        self.assertEqual(cache.live_size(), 1)
        self.assertTrue(
            cache.resume(ticket2, "TLS1.3",
                         "TLS_CHACHA20_POLY1305_SHA256").reused)


class TestExpiry(unittest.TestCase):
    def test_expired_ticket_rejected(self):
        cache, clock, _ = make_cache(ttl=60.0)
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        clock.advance(61.0)
        result = cache.resume(ticket, "TLS1.3", "TLS_AES_256_GCM_SHA384")
        self.assertFalse(result.reused)
        self.assertEqual(result.reason, RejectReason.EXPIRED_TICKET)
        self.assertIn("expired", result.detail)

    def test_lazy_cleanup_removes_dead_entries(self):
        cache, clock, _ = make_cache(ttl=10.0)
        cache.store("a", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        cache.store("b", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        clock.advance(11.0)
        self.assertEqual(cache.purge_expired(), 2)
        self.assertEqual(cache.live_size(), 0)
        self.assertEqual(cache.stats.lazy_expiries, 2)

    def test_boundary_exactly_at_expiry_is_expired(self):
        cache, clock, _ = make_cache(ttl=60.0)
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        clock.advance(60.0)  # now == expires_at
        self.assertFalse(
            cache.resume(ticket, "TLS1.3",
                         "TLS_AES_256_GCM_SHA384").reused)

    def test_just_before_expiry_still_valid(self):
        cache, clock, _ = make_cache(ttl=60.0)
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        clock.advance(59.999)
        self.assertTrue(
            cache.resume(ticket, "TLS1.3",
                         "TLS_AES_256_GCM_SHA384").reused)


class TestCapacity(unittest.TestCase):
    def test_lru_eviction_when_full(self):
        cache, _, _ = make_cache(max_entries=3)
        tickets = [
            cache.store(f"s{i}", "TLS1.3", "TLS_AES_256_GCM_SHA384")
            for i in range(4)
        ]
        self.assertEqual(cache.live_size(), 3)
        self.assertEqual(cache.stats.evictions, 1)
        # s0 was the LRU victim -> EVICTED; s1..s3 still resumable.
        r0 = cache.resume(tickets[0], "TLS1.3", "TLS_AES_256_GCM_SHA384")
        self.assertFalse(r0.reused)
        self.assertEqual(r0.reason, RejectReason.EVICTED)
        for t in tickets[1:]:
            self.assertTrue(
                cache.resume(t, "TLS1.3",
                             "TLS_AES_256_GCM_SHA384").reused)

    def test_access_refreshes_lru_order(self):
        cache, _, _ = make_cache(max_entries=3)
        t0 = cache.store("s0", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        cache.store("s2", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        # Touch s0 so s1 becomes the LRU entry.
        self.assertTrue(
            cache.resume(t0, "TLS1.3", "TLS_AES_256_GCM_SHA384").reused)
        t3 = cache.store("s3", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        # s1 evicted, s0 survived.
        self.assertTrue(
            cache.resume(t0, "TLS1.3", "TLS_AES_256_GCM_SHA384").reused)
        self.assertTrue(
            cache.resume(t3, "TLS1.3", "TLS_AES_256_GCM_SHA384").reused)

    def test_expired_entries_reaped_before_lru_victim(self):
        cache, clock, _ = make_cache(max_entries=2, ttl=10.0)
        cache.store("old", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        clock.advance(20.0)  # "old" is dead
        cache.store("new1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        cache.store("new2", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        # Capacity was never spent on the dead entry.
        self.assertEqual(cache.stats.evictions, 0)
        self.assertEqual(cache.live_size(), 2)

    def test_unknown_session_rejected(self):
        cache, _, key = make_cache()
        # Well-formed, correctly signed ticket for a session never stored.
        ticket = issue_ticket(key, "ghost", "TLS1.3",
                              "TLS_AES_256_GCM_SHA384", 0.0, 1e12)
        result = cache.resume(ticket, "TLS1.3", "TLS_AES_256_GCM_SHA384")
        self.assertFalse(result.reused)
        self.assertEqual(result.reason, RejectReason.UNKNOWN_SESSION)


class TestBinding(unittest.TestCase):
    def test_protocol_version_mismatch(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        result = cache.resume(ticket, "TLS1.2", "TLS_AES_256_GCM_SHA384")
        self.assertFalse(result.reused)
        self.assertEqual(result.reason, RejectReason.PROTOCOL_MISMATCH)
        self.assertIn("TLS1.2", result.detail)
        self.assertIn("TLS1.3", result.detail)

    def test_cipher_suite_mismatch(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        result = cache.resume(ticket, "TLS1.3",
                              "TLS_CHACHA20_POLY1305_SHA256")
        self.assertFalse(result.reused)
        self.assertEqual(result.reason, RejectReason.CIPHER_MISMATCH)
        self.assertIn("CHACHA20", result.detail)

    def test_both_mismatch_reports_protocol_first(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        result = cache.resume(ticket, "TLS1.2",
                              "TLS_CHACHA20_POLY1305_SHA256")
        self.assertEqual(result.reason, RejectReason.PROTOCOL_MISMATCH)


class TestTamperDetection(unittest.TestCase):
    def _forge(self, ticket: str, mutate) -> str:
        payload_b64, mac_b64 = ticket.split(".")
        pad = "=" * (-len(payload_b64) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + pad).decode())
        mutate(payload)
        raw = json.dumps(payload, separators=(",", ":"),
                         sort_keys=True).encode()
        new_b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return new_b64 + "." + mac_b64  # original MAC, tampered payload

    def test_single_bit_flip_rejected(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        payload_b64, mac_b64 = ticket.split(".")
        pad = "=" * (-len(payload_b64) % 4)
        raw = bytearray(base64.urlsafe_b64decode(payload_b64 + pad))
        raw[0] ^= 0x01  # guaranteed byte-level change
        forged = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode() \
            + "." + mac_b64
        result = cache.resume(forged, "TLS1.3", "TLS_AES_256_GCM_SHA384")
        self.assertFalse(result.reused)
        self.assertEqual(result.reason, RejectReason.FORGED)

    def test_payload_rewrite_with_stale_mac_rejected(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        forged = self._forge(ticket, lambda p: p.update(ver="TLS1.2"))
        result = cache.resume(forged, "TLS1.2", "TLS_AES_256_GCM_SHA384")
        self.assertFalse(result.reused)
        self.assertEqual(result.reason, RejectReason.FORGED)

    def test_expiry_extension_attack_rejected(self):
        cache, clock, _ = make_cache(ttl=60.0)
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        clock.advance(3600.0)
        forged = self._forge(ticket, lambda p: p.update(exp=1e12))
        result = cache.resume(forged, "TLS1.3", "TLS_AES_256_GCM_SHA384")
        self.assertFalse(result.reused)
        self.assertEqual(result.reason, RejectReason.FORGED)

    def test_wrong_server_key_rejected(self):
        cache, _, _ = make_cache()
        ticket = cache.store("s1", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        other_key = secrets.token_bytes(32)
        with self.assertRaises(TicketError):
            verify_ticket(other_key, ticket)

    def test_malformed_tickets_rejected(self):
        cache, _, _ = make_cache()
        for bad in ("", "no-dot-here", "a.b.c", ".", "x.", ".y",
                    "!!!.@@@", None, 12345):
            result = cache.resume(bad, "TLS1.3", "TLS_AES_256_GCM_SHA384")
            self.assertFalse(result.reused, msg=repr(bad))
            self.assertIn(result.reason,
                          (RejectReason.MALFORMED, RejectReason.FORGED))

    def test_forged_rejection_rate_is_100_percent(self):
        cache, _, _ = make_cache(max_entries=64)
        tickets = [
            cache.store(f"s{i}", "TLS1.3", "TLS_AES_256_GCM_SHA384")
            for i in range(10)
        ]
        attempts = 0
        for ticket in tickets:
            for mutate in (
                lambda p: p.update(ver="TLS1.2"),
                lambda p: p.update(cs="TLS_CHACHA20_POLY1305_SHA256"),
                lambda p: p.update(exp=p["exp"] + 9999),
                lambda p: p.update(sid=p["sid"] + "x"),
            ):
                forged = self._forge(ticket, mutate)
                result = cache.resume(forged, "TLS1.3",
                                      "TLS_AES_256_GCM_SHA384")
                attempts += 1
                self.assertFalse(result.reused)
                self.assertEqual(result.reason, RejectReason.FORGED)
        self.assertEqual(attempts, 40)
        self.assertEqual(cache.stats.forged_or_malformed, 40)
        self.assertEqual(cache.stats.reused, 0)


class TestValidation(unittest.TestCase):
    def test_invalid_constructor_args(self):
        key = secrets.token_bytes(32)
        with self.assertRaises(ValueError):
            SessionCache(key, max_entries=0)
        with self.assertRaises(ValueError):
            SessionCache(key, ttl_seconds=0)
        with self.assertRaises(ValueError):
            SessionCache(b"short")

    def test_empty_fields_rejected(self):
        cache, _, _ = make_cache()
        with self.assertRaises(ValueError):
            cache.store("", "TLS1.3", "TLS_AES_256_GCM_SHA384")
        with self.assertRaises(ValueError):
            cache.store("s1", "", "TLS_AES_256_GCM_SHA384")
        with self.assertRaises(ValueError):
            cache.store("s1", "TLS1.3", "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
