"""Self-tests for the one-time verifier-bound authorization-code exchange."""

import threading
import unittest

from authcode import (
    AuthorizationServer,
    Denial,
    ExchangeDenied,
    Reason,
    VERIFIER_MAX_LEN,
    VERIFIER_MIN_LEN,
    compute_challenge,
    generate_verifier,
    make_pkce_pair,
)

UNRESERVED = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class HappyPathTests(unittest.TestCase):
    def test_s256_round_trip(self):
        clock = FakeClock()
        server = AuthorizationServer(code_ttl=600, clock=clock)
        pair = make_pkce_pair("S256")
        code = server.authorize("client-a", pair["code_challenge"], "S256")

        token = server.exchange(code, pair["code_verifier"])

        self.assertEqual(token["token_type"], "Bearer")
        self.assertEqual(token["client_id"], "client-a")
        self.assertGreaterEqual(len(token["access_token"]), 43)
        self.assertEqual(server.denials, [])

    def test_plain_round_trip(self):
        server = AuthorizationServer(clock=FakeClock())
        verifier = generate_verifier()
        code = server.authorize("client-a", compute_challenge(verifier, "plain"), "plain")

        token = server.exchange(code, verifier)

        self.assertTrue(token["access_token"])

    def test_token_just_before_expiry_boundary(self):
        clock = FakeClock()
        server = AuthorizationServer(code_ttl=60, clock=clock)
        pair = make_pkce_pair()
        code = server.authorize("client-a", pair["code_challenge"])
        clock.advance(59.999)

        self.assertIn("access_token", server.exchange(code, pair["code_verifier"]))


class SingleUseTests(unittest.TestCase):
    def _issued_code(self):
        self.server = AuthorizationServer(clock=FakeClock())
        self.pair = make_pkce_pair()
        return self.server.authorize("client-a", self.pair["code_challenge"])

    def test_replay_is_rejected_and_recorded(self):
        server, pair = AuthorizationServer(clock=FakeClock()), make_pkce_pair()
        code = server.authorize("client-a", pair["code_challenge"])
        server.exchange(code, pair["code_verifier"])

        with self.assertRaises(ExchangeDenied) as caught:
            server.exchange(code, pair["code_verifier"])

        self.assertEqual(caught.exception.reason, Reason.CODE_ALREADY_USED)
        ledger = server.denials
        self.assertEqual(len(ledger), 1)
        self.assertIsInstance(ledger[0], Denial)
        self.assertEqual(ledger[0].reason, Reason.CODE_ALREADY_USED)
        self.assertTrue(ledger[0].code_hint.startswith(code[:8]))

    def test_verifier_tamper_consumes_code(self):
        server, pair = AuthorizationServer(clock=FakeClock()), make_pkce_pair()
        code = server.authorize("client-a", pair["code_challenge"])
        tampered = ("a" if pair["code_verifier"][0] != "a" else "b") + pair["code_verifier"][1:]

        with self.assertRaises(ExchangeDenied) as caught:
            server.exchange(code, tampered)
        self.assertEqual(caught.exception.reason, Reason.VERIFIER_MISMATCH)

        # The code is now spent even though the first attempt failed: the
        # attacker cannot keep guessing verifiers against a leaked code.
        with self.assertRaises(ExchangeDenied) as replay:
            server.exchange(code, pair["code_verifier"])
        self.assertEqual(replay.exception.reason, Reason.CODE_ALREADY_USED)

    def test_concurrent_replays_exactly_one_wins(self):
        server, pair = AuthorizationServer(clock=FakeClock()), make_pkce_pair()
        code = server.authorize("client-a", pair["code_challenge"])
        outcomes = []

        def redeem():
            try:
                outcomes.append(("ok", server.exchange(code, pair["code_verifier"])))
            except ExchangeDenied as exc:
                outcomes.append(("denied", exc.reason))

        threads = [threading.Thread(target=redeem) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(outcomes), 20)
        self.assertEqual(sum(1 for kind, _ in outcomes if kind == "ok"), 1)
        self.assertTrue(all(r == Reason.CODE_ALREADY_USED
                            for kind, r in outcomes if kind == "denied"))


class VerifierCheckTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.server = AuthorizationServer(clock=self.clock)
        self.pair = make_pkce_pair()
        self.code = self.server.authorize("client-a", self.pair["code_challenge"])

    def test_missing_verifier_none(self):
        with self.assertRaises(ExchangeDenied) as c:
            self.server.exchange(self.code, None)
        self.assertEqual(c.exception.reason, Reason.VERIFIER_MISSING)

    def test_missing_verifier_empty_string(self):
        with self.assertRaises(ExchangeDenied) as c:
            self.server.exchange(self.code, "")
        self.assertIn(c.exception.reason,
                      {Reason.VERIFIER_MISSING, Reason.VERIFIER_MALFORMED})

    def test_malformed_verifier_too_short(self):
        with self.assertRaises(ExchangeDenied) as c:
            self.server.exchange(self.code, "a" * (VERIFIER_MIN_LEN - 1))
        self.assertEqual(c.exception.reason, Reason.VERIFIER_MALFORMED)

    def test_malformed_verifier_bad_character(self):
        bad = "a" * (VERIFIER_MIN_LEN - 1) + "#"
        self.assertEqual(len(bad), VERIFIER_MIN_LEN)
        with self.assertRaises(ExchangeDenied) as c:
            self.server.exchange(self.code, bad)
        self.assertEqual(c.exception.reason, Reason.VERIFIER_MALFORMED)

    def test_malformed_verifier_too_long(self):
        with self.assertRaises(ExchangeDenied) as c:
            self.server.exchange(self.code, "a" * (VERIFIER_MAX_LEN + 1))
        self.assertEqual(c.exception.reason, Reason.VERIFIER_MALFORMED)

    def test_algorithm_mismatch_unsupported_stored_method(self):
        # Simulate legacy/corrupted server storage advertising a method the
        # current build does not support.
        self.server._codes[self.code].method = "MD5"
        with self.assertRaises(ExchangeDenied) as c:
            self.server.exchange(self.code, self.pair["code_verifier"])
        self.assertEqual(c.exception.reason, Reason.METHOD_MISMATCH)


class CodeLifecycleTests(unittest.TestCase):
    def test_expired_code_rejected(self):
        clock = FakeClock()
        server = AuthorizationServer(code_ttl=60, clock=clock)
        pair = make_pkce_pair()
        code = server.authorize("client-a", pair["code_challenge"])
        clock.advance(60)  # exactly at expiry -> expired

        with self.assertRaises(ExchangeDenied) as c:
            server.exchange(code, pair["code_verifier"])
        self.assertEqual(c.exception.reason, Reason.CODE_EXPIRED)

    def test_unknown_code_rejected(self):
        server = AuthorizationServer(clock=FakeClock())
        with self.assertRaises(ExchangeDenied) as c:
            server.exchange("never-issued", generate_verifier())
        self.assertEqual(c.exception.reason, Reason.UNKNOWN_CODE)

    def test_none_code_rejected(self):
        server = AuthorizationServer(clock=FakeClock())
        with self.assertRaises(ExchangeDenied) as c:
            server.exchange(None, generate_verifier())
        self.assertEqual(c.exception.reason, Reason.UNKNOWN_CODE)

    def test_check_order_expired_before_used(self):
        clock = FakeClock()
        server = AuthorizationServer(code_ttl=10, clock=clock)
        pair = make_pkce_pair()
        code = server.authorize("client-a", pair["code_challenge"])
        server.exchange(code, pair["code_verifier"])
        clock.advance(100)

        with self.assertRaises(ExchangeDenied) as c:
            server.exchange(code, pair["code_verifier"])
        self.assertEqual(c.exception.reason, Reason.CODE_EXPIRED)

    def test_authorize_rejects_unknown_method(self):
        server = AuthorizationServer(clock=FakeClock())
        with self.assertRaises(ValueError):
            server.authorize("client-a", "challenge", "RS256")


class VerifierGenerationTests(unittest.TestCase):
    def test_length_charset_and_uniqueness(self):
        seen = set()
        for _ in range(5000):
            verifier = generate_verifier()
            self.assertEqual(len(verifier), VERIFIER_MIN_LEN)
            self.assertTrue(set(verifier) <= UNRESERVED)
            seen.add(verifier)
        self.assertEqual(len(seen), 5000)  # no CSPRNG collisions/repeats

    def test_challenge_is_not_the_verifier(self):
        pair = make_pkce_pair("S256")
        self.assertNotEqual(pair["code_challenge"], pair["code_verifier"])
        self.assertEqual(len(pair["code_challenge"]), 43)
        # deterministic derivation: same verifier -> same challenge
        self.assertEqual(
            compute_challenge(pair["code_verifier"]), pair["code_challenge"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
