"""Self-tests for the certchain library.

Run from the repo root:   python3 -m unittest discover -s tests -v
Fixtures are (re)generated with:  tests/gen_fixtures.sh
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from certchain import TrustStore, load_certificates, verify          # noqa: E402
from certchain.validator import _dns_matches, check_name_constraints  # noqa: E402
from certchain.x509 import NameConstraints, GeneralName               # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def cert(name):
    (c,) = load_certificates(os.path.join(FIX, name))
    return c


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


# fixed validation time: every "normal" fixture is valid then
NOW = utc(2026, 6, 1)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_a = cert("root-a.crt")
        cls.root_b = cert("root-b.crt")
        cls.root_nc = cert("root-nc.crt")
        cls.int_ca1 = cert("int-ca1.crt")
        cls.int_x_a = cert("int-x-by-a.crt")
        cls.int_x_b = cert("int-x-by-b.crt")
        cls.int_exp = cert("int-exp.crt")
        cls.int_nc = cert("int-nc.crt")
        cls.int_p0 = cert("int-p0.crt")
        cls.sub_p0 = cert("sub-p0.crt")
        cls.store_a = TrustStore([cls.root_a])
        cls.store_nc = TrustStore([cls.root_nc])

    def check(self, leaf, pool, store=None, when=NOW):
        return verify(leaf, pool, store or self.store_a, when=when)


class TestNormalChain(Base):
    def test_two_level_chain_ok(self):
        res = self.check(cert("leaf-good.crt"), [self.int_ca1])
        self.assertTrue(res.ok, res.describe())
        self.assertEqual([c.subject.get("2.5.4.3")[0] for c in res.chain],
                         ["Good Leaf", "Intermediate CA 1", "Root A"])

    def test_irrelevant_certs_in_pool_ignored(self):
        pool = [self.int_ca1, self.int_exp, self.root_b, self.sub_p0]
        res = self.check(cert("leaf-good.crt"), pool)
        self.assertTrue(res.ok, res.describe())
        self.assertEqual(len(res.chain), 3)

    def test_anchor_itself_validates(self):
        res = self.check(self.root_a, [])
        self.assertTrue(res.ok, res.describe())
        self.assertEqual(len(res.chain), 1)

    def test_ecdsa_leaf_ok(self):
        res = self.check(cert("leaf-ec.crt"), [self.int_ca1])
        self.assertTrue(res.ok, res.describe())


class TestCrossSigning(Base):
    def test_chain_via_trusted_root_accepted(self):
        # both cross certificates available: builder must pick the one
        # that leads to the configured anchor (Root A)
        # Root B's certificate is present as well, so the builder sees BOTH
        # complete paths; only the one terminating at the configured anchor
        # must be accepted.
        res = self.check(cert("leaf-cross.crt"),
                         [self.int_x_a, self.int_x_b, self.root_b])
        self.assertTrue(res.ok, res.describe())
        issuers = [c.issuer.get("2.5.4.3")[0] for c in res.chain]
        self.assertIn("Root A", issuers)
        self.assertNotIn("Root B", issuers)
        self.assertEqual(res.tried_chains, 2)  # both paths were explored

    def test_chain_via_untrusted_root_rejected(self):
        # only the Root-B cross certificate is available
        res = self.check(cert("leaf-cross.crt"), [self.int_x_b, self.root_b])
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "INVALID")
        self.assertIn("UNTRUSTED_ROOT", [e.code for e in res.errors])
        self.assertIn("Root B", res.errors[0].message)

    def test_cross_cert_without_any_root_reports_missing(self):
        res = self.check(cert("leaf-cross.crt"), [self.int_x_b])
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "MISSING_INTERMEDIATE")
        self.assertIn("Root B", res.errors[0].message)


class TestTrustAnchorPinning(Base):
    def test_chain_to_other_root_rejected(self):
        # leaf issued by Root B; Root B cert is provided but NOT configured
        res = self.check(cert("leaf-other.crt"), [self.root_b])
        self.assertFalse(res.ok)
        self.assertIn("UNTRUSTED_ROOT", [e.code for e in res.errors])

    def test_wrong_anchor_set_rejected(self):
        # only Root B configured; the Root A chain must fail
        store_b = TrustStore([self.root_b])
        res = verify(cert("leaf-good.crt"), [self.int_ca1], store_b, when=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "MISSING_INTERMEDIATE")
        self.assertIn("Root A", res.errors[0].message)

    def test_same_subject_different_key_not_trusted(self):
        # an anchor that copies Root A's name but carries a *different* key
        # must not be trusted (pinning is name + public key)
        renamed = self.root_b
        renamed.subject = self.root_a.subject
        store = TrustStore([renamed])
        res = verify(cert("leaf-good.crt"), [self.int_ca1], store, when=NOW)
        self.assertFalse(res.ok)


class TestMissingIntermediate(Base):
    def test_missing_intermediate_identified(self):
        res = self.check(cert("leaf-good.crt"), [])
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "MISSING_INTERMEDIATE")
        err = res.errors[0]
        self.assertEqual(err.code, "MISSING_INTERMEDIATE")
        # must name BOTH the certificate whose issuer is missing ...
        self.assertIn("Good Leaf", err.message)
        # ... and the issuer (subject DN) that is needed
        self.assertIn("Intermediate CA 1", err.message)
        # AKI of the missing issuer is reported to help locate it
        self.assertIn("authorityKeyId=", err.message)

    def test_missing_top_intermediate_identified(self):
        # leaf -> int-ca1 present, but root-a's issuer cert for int-ca1 is
        # the anchor itself, so drop the anchor instead: use empty-ish pool
        res = self.check(cert("leaf-p0.crt"), [self.int_p0])  # sub-p0 missing
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "MISSING_INTERMEDIATE")
        self.assertIn("Sub CA Under PathLen0", res.errors[0].message)


class TestValidity(Base):
    def test_expired_intermediate_rejected(self):
        res = self.check(cert("leaf-exp.crt"), [self.int_exp])
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "INVALID")
        expired = [e for e in res.errors if e.code == "EXPIRED"]
        self.assertTrue(expired, res.describe())
        self.assertTrue(
            any("Intermediate Expired" in e.cert_subject for e in expired),
            res.describe())

    def test_expired_intermediate_rejected_even_if_leaf_also_expired(self):
        # both are expired; the intermediate must appear in the errors
        res = self.check(cert("leaf-exp.crt"), [self.int_exp])
        subjects = {e.cert_subject for e in res.errors if e.code == "EXPIRED"}
        self.assertTrue(any("Intermediate Expired" in s for s in subjects))

    def test_not_yet_valid_leaf(self):
        res = self.check(cert("leaf-fixed.crt"), [self.int_ca1],
                         when=utc(2025, 12, 31))
        self.assertFalse(res.ok)
        self.assertIn("NOT_YET_VALID", [e.code for e in res.errors])

    def test_valid_at_not_before_inclusive(self):
        res = self.check(cert("leaf-fixed.crt"), [self.int_ca1],
                         when=utc(2026, 1, 1))
        self.assertTrue(res.ok, res.describe())

    def test_invalid_at_not_after_exactly(self):
        # RFC 5280: the certificate is invalid AT the notAfter instant
        res = self.check(cert("leaf-fixed.crt"), [self.int_ca1],
                         when=utc(2027, 1, 1))
        self.assertFalse(res.ok)
        self.assertIn("EXPIRED", [e.code for e in res.errors])

    def test_valid_one_second_before_not_after(self):
        res = self.check(cert("leaf-fixed.crt"), [self.int_ca1],
                         when=datetime(2026, 12, 31, 23, 59, 59,
                                       tzinfo=timezone.utc))
        self.assertTrue(res.ok, res.describe())


class TestSelfSigned(Base):
    def test_self_signed_leaf_rejected(self):
        res = self.check(cert("leaf-self.crt"), [])
        self.assertFalse(res.ok)
        self.assertIn("UNTRUSTED_ROOT", [e.code for e in res.errors])
        self.assertIn("Self Signed Leaf", res.errors[0].message)

    def test_self_signed_leaf_rejected_even_in_pool(self):
        leaf = cert("leaf-self.crt")
        res = self.check(leaf, [leaf])
        self.assertFalse(res.ok)


class TestNameConstraints(Base):
    POOL = None  # built in setUpClass of subclass

    def check_nc(self, leaf_name):
        return verify(cert(leaf_name), [self.int_nc], self.store_nc, when=NOW)

    def test_within_permitted_subtree_ok(self):
        res = self.check_nc("leaf-nc-good.crt")
        self.assertTrue(res.ok, res.describe())

    def test_outside_permitted_subtree_rejected(self):
        res = self.check_nc("leaf-nc-outside.crt")
        self.assertFalse(res.ok)
        errs = [e for e in res.errors if e.code == "NAME_CONSTRAINT_VIOLATION"]
        self.assertTrue(errs, res.describe())
        self.assertIn("evil.com", errs[0].message)
        self.assertIn("permitted", errs[0].message)

    def test_excluded_subtree_rejected(self):
        res = self.check_nc("leaf-nc-excluded.crt")
        self.assertFalse(res.ok)
        errs = [e for e in res.errors if e.code == "NAME_CONSTRAINT_VIOLATION"]
        self.assertTrue(errs, res.describe())
        self.assertIn("bad.example.com", errs[0].message)
        self.assertIn("excluded", errs[0].message)


class TestNameConstraintMatching(unittest.TestCase):
    """Unit tests for the matching rules themselves (edge cases)."""

    def test_dns_matching_rules(self):
        self.assertTrue(_dns_matches("example.com", "example.com"))
        self.assertTrue(_dns_matches("www.example.com", "example.com"))
        self.assertTrue(_dns_matches("a.b.example.com", "example.com"))
        self.assertFalse(_dns_matches("notexample.com", "example.com"))
        self.assertFalse(_dns_matches("evil-example.com", "example.com"))
        self.assertFalse(_dns_matches("example.com.evil.com", "example.com"))
        # leading-dot form: subdomains only, not the apex
        self.assertTrue(_dns_matches("www.example.com", ".example.com"))
        self.assertFalse(_dns_matches("example.com", ".example.com"))
        # case and trailing dot insensitivity
        self.assertTrue(_dns_matches("WWW.Example.COM.", "EXAMPLE.com."))

    def test_empty_subject_with_permitted_dirname(self):
        nc = NameConstraints(
            permitted=(GeneralName("dir", cert("root-nc.crt").subject),))
        leaf = cert("leaf-nc-good.crt")
        leaf.subject = type(leaf.subject)(raw=b"0\x00", rdns=())
        violation = check_name_constraints(leaf, nc)
        self.assertIsNotNone(violation)


class TestPathLen(Base):
    def test_pathlen_zero_violated(self):
        # int-p0 has pathlen:0 but sub-p0 (a CA) sits below it
        res = self.check(cert("leaf-p0.crt"), [self.sub_p0, self.int_p0])
        self.assertFalse(res.ok)
        errs = [e for e in res.errors if e.code == "PATH_LEN_EXCEEDED"]
        self.assertTrue(errs, res.describe())
        self.assertIn("Intermediate PathLen0", errs[0].cert_subject)


class TestSignatureIntegrity(Base):
    def test_tampered_leaf_rejected(self):
        leaf = cert("leaf-good.crt")
        raw = bytearray(leaf.der)
        raw[-10] ^= 0x01  # flip a bit inside the signature
        from certchain.x509 import parse_certificate
        tampered = parse_certificate(bytes(raw))
        res = self.check(tampered, [self.int_ca1])
        self.assertFalse(res.ok)

    def test_unknown_signature_algorithm_rejected(self):
        leaf = cert("leaf-good.crt")
        leaf.sig_alg_oid = "1.2.3.4.5"
        res = self.check(leaf, [self.int_ca1])
        self.assertFalse(res.ok)


if __name__ == "__main__":
    unittest.main()
