"""Self-tests for password hashing and in-place parameter upgrades.

Run with:  python -m unittest discover -s tests -v
(or simply: python tests/test_pwhash.py)
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pwhash import core
from pwhash.core import (
    CURRENT_ALG,
    InvalidRecordError,
    UnsupportedAlgorithmError,
    hash_password,
    needs_upgrade,
    normalize_record,
    verify_and_upgrade,
    verify_password,
)
from pwhash.userdb import UserDB

# Fast parameters for tests; production default is 600000.
TEST_PARAMS = {"iterations": 1000}
LEGACY_PARAMS = {"iterations": 100}


class _FastParamsMixin:
    def setUp(self):
        self._saved_params = core.CURRENT_PARAMS
        self._saved_iters = core.DEFAULT_ITERATIONS
        core.CURRENT_PARAMS = dict(TEST_PARAMS)
        core.DEFAULT_ITERATIONS = TEST_PARAMS["iterations"]

    def tearDown(self):
        core.CURRENT_PARAMS = self._saved_params
        core.DEFAULT_ITERATIONS = self._saved_iters

    @staticmethod
    def legacy_record(password):
        return normalize_record(
            hash_password(password, iterations=LEGACY_PARAMS["iterations"])
        )


class TestFirstSetup(_FastParamsMixin, unittest.TestCase):
    def test_new_record_is_self_describing(self):
        rec = hash_password("hunter2")
        self.assertEqual(rec["v"], 1)
        self.assertEqual(rec["alg"], CURRENT_ALG)
        self.assertEqual(rec["params"], TEST_PARAMS)
        self.assertIn("salt", rec)
        self.assertIn("hash", rec)

    def test_verify_right_and_wrong_password(self):
        rec = hash_password("hunter2")
        self.assertTrue(verify_password("hunter2", rec))
        self.assertFalse(verify_password("Hunter2", rec))
        self.assertFalse(verify_password("", rec))

    def test_fresh_record_needs_no_upgrade(self):
        rec = hash_password("hunter2")
        self.assertFalse(needs_upgrade(rec))
        ok, upgraded = verify_and_upgrade("hunter2", rec)
        self.assertTrue(ok)
        self.assertIsNone(upgraded)

    def test_db_create_and_login(self):
        db = UserDB()
        db.create("alice", "s3cret")
        self.assertTrue(db.login("alice", "s3cret").ok)
        self.assertFalse(db.login("alice", "nope").ok)
        self.assertFalse(db.login("ghost", "s3cret").ok)


class TestPerUserRandomSalt(_FastParamsMixin, unittest.TestCase):
    def test_same_password_different_users_different_hashes(self):
        rec_a = hash_password("same-password")
        rec_b = hash_password("same-password")
        # Explicit assertion required by the spec.
        self.assertNotEqual(rec_a["salt"], rec_b["salt"])
        self.assertNotEqual(rec_a["hash"], rec_b["hash"])
        # Both records still verify.
        self.assertTrue(verify_password("same-password", rec_a))
        self.assertTrue(verify_password("same-password", rec_b))

    def test_random_salt_over_many_records(self):
        salts = {hash_password("pw")["salt"] for _ in range(64)}
        self.assertEqual(len(salts), 64)

    def test_deterministic_given_same_salt(self):
        rec = hash_password("pw")
        salt = __import__("base64").b64decode(rec["salt"])
        import hashlib

        digest = hashlib.pbkdf2_hmac(
            "sha256", b"pw", salt, rec["params"]["iterations"], dklen=32
        )
        import base64

        self.assertEqual(rec["hash"], base64.b64encode(digest).decode())


class TestLegacyVerification(_FastParamsMixin, unittest.TestCase):
    def test_legacy_params_verify_with_record_params(self):
        rec = self.legacy_record("old-pw")
        self.assertEqual(rec["params"], LEGACY_PARAMS)
        self.assertTrue(needs_upgrade(rec))
        # Verification must use the iterations stored in the record,
        # not the current global parameters.
        self.assertTrue(verify_password("old-pw", rec))
        self.assertFalse(verify_password("old-pw!", rec))

    def test_legacy_record_does_not_verify_with_current_iterations(self):
        rec = self.legacy_record("old-pw")
        tampered = json.loads(json.dumps(rec))
        tampered["params"] = dict(TEST_PARAMS)  # params lie about the hash
        self.assertFalse(verify_password("old-pw", tampered))


class TestParameterUpgrade(_FastParamsMixin, unittest.TestCase):
    def test_verify_and_upgrade_core(self):
        rec = self.legacy_record("old-pw")
        before = json.loads(json.dumps(rec))

        ok, upgraded = verify_and_upgrade("old-pw", rec)
        self.assertTrue(ok)
        self.assertIsNotNone(upgraded)
        self.assertEqual(upgraded["params"], TEST_PARAMS)
        self.assertEqual(upgraded["salt"], before["salt"])  # salt preserved
        self.assertNotEqual(upgraded["hash"], before["hash"])  # hash recomputed
        self.assertFalse(needs_upgrade(upgraded))

        # The original record is untouched; caller persists the new one.
        self.assertEqual(rec["params"], LEGACY_PARAMS)

    def test_failed_login_does_not_upgrade(self):
        rec = self.legacy_record("old-pw")
        ok, upgraded = verify_and_upgrade("wrong", rec)
        self.assertFalse(ok)
        self.assertIsNone(upgraded)
        self.assertEqual(rec["params"], LEGACY_PARAMS)

    def test_db_login_upgrades_in_place_with_before_after(self):
        db = UserDB()
        db.set_raw_record("bob", self.legacy_record("hunter2"))
        self.assertTrue(db.needs_upgrade("bob"))

        result = db.login("bob", "hunter2")
        self.assertTrue(result.ok)
        self.assertTrue(result.upgraded)
        # Before / after content is exposed for auditing.
        self.assertEqual(result.before["params"], LEGACY_PARAMS)
        self.assertEqual(result.after["params"], TEST_PARAMS)
        self.assertEqual(result.before["salt"], result.after["salt"])
        self.assertNotEqual(result.before["hash"], result.after["hash"])

        # Stored record was replaced in place.
        self.assertFalse(db.needs_upgrade("bob"))
        self.assertEqual(db.get_record("bob"), result.after)

        # Second login: correct password, no further upgrade.
        again = db.login("bob", "hunter2")
        self.assertTrue(again.ok)
        self.assertFalse(again.upgraded)

        # Wrong password after upgrade leaves the new record intact.
        bad = db.login("bob", "bad")
        self.assertFalse(bad.ok)
        self.assertFalse(bad.upgraded)

    def test_upgrade_persists_across_save_load(self):
        db = UserDB()
        db.set_raw_record("carol", self.legacy_record("hunter2"))
        db.login("carol", "hunter2")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "users.json")
            db.save(path)
            loaded = UserDB.load(path)
            self.assertFalse(loaded.needs_upgrade("carol"))
            self.assertTrue(loaded.login("carol", "hunter2").ok)


class TestCorruptAndBoundaryRecords(_FastParamsMixin, unittest.TestCase):
    CORRUPT_RECORDS = [
        "not-a-dict",
        {},
        {"v": 1},
        {"v": 1, "alg": CURRENT_ALG, "params": {"iterations": 0},
         "salt": "AAAA", "hash": "AAAA"},
        {"v": 1, "alg": CURRENT_ALG, "params": {"iterations": -5},
         "salt": "AAAA", "hash": "AAAA"},
        {"v": 1, "alg": CURRENT_ALG, "params": {"iterations": "1000"},
         "salt": "AAAA", "hash": "AAAA"},
        {"v": 1, "alg": CURRENT_ALG, "params": {},
         "salt": "AAAA", "hash": "AAAA"},
        {"v": 1, "alg": CURRENT_ALG, "params": {"iterations": 1000},
         "salt": "!!!not-b64", "hash": "AAAA"},
        {"v": 1, "alg": CURRENT_ALG, "params": {"iterations": 1000},
         "salt": "AAAA", "hash": "AAAA"},  # wrong hash length
    ]

    def test_normalize_rejects_corrupt_records(self):
        for rec in self.CORRUPT_RECORDS:
            with self.subTest(rec=rec):
                with self.assertRaises(InvalidRecordError):
                    normalize_record(rec)

    def test_unknown_algorithm(self):
        rec = hash_password("pw")
        rec["alg"] = "argon2id-v19"
        with self.assertRaises(UnsupportedAlgorithmError):
            normalize_record(rec)
        with self.assertRaises(UnsupportedAlgorithmError):
            verify_password("pw", rec)

    def test_db_corrupt_record_never_authenticates(self):
        db = UserDB()
        for rec in self.CORRUPT_RECORDS:
            db.set_raw_record("dave", rec)
            with self.subTest(rec=rec):
                result = db.login("dave", "anything")
                self.assertFalse(result.ok)
                self.assertFalse(result.upgraded)

    def test_db_corrupt_helper(self):
        db = UserDB()
        db.create("erin", "pw")
        db.corrupt("erin")
        self.assertFalse(db.login("erin", "pw").ok)

    def test_tampered_record_fails(self):
        rec = hash_password("pw")
        rec["hash"] = rec["hash"].replace("A", "B") if "A" in rec["hash"] \
            else ("B" + rec["hash"][1:])
        self.assertFalse(verify_password("pw", rec))

        rec2 = hash_password("pw")
        rec2["salt"] = hash_password("pw")["salt"]
        self.assertFalse(verify_password("pw", rec2))

    def test_hash_password_rejects_bad_iterations(self):
        for bad in (0, -1, 1.5, "1000", True, None):
            with self.subTest(bad=bad):
                if bad is None:
                    continue  # None means "use default"
                with self.assertRaises((ValueError, TypeError)):
                    hash_password("pw", iterations=bad)

    def test_empty_and_unicode_passwords(self):
        for pw in ("", "口令🔑", "x" * 4096):
            rec = hash_password(pw)
            self.assertTrue(verify_password(pw, rec))
            self.assertFalse(verify_password(pw + " ", rec))


if __name__ == "__main__":
    unittest.main(verbosity=2)
