"""Self-tests for the format-preserving encryption library.

Run:  python3 -m unittest discover -s tests -v
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fpe.fpe_core import (
    ALPHANUMERIC,
    DIGITS,
    FeistelFPE,
    InvalidInputError,
    InvalidKeyError,
    InvalidTweakError,
)
from fpe.field_scheme import FieldScheme, InvalidCheckDigitError

KEY = bytes(range(32))
KEY2 = bytes(reversed(range(32)))
IV1 = b"iv-0001"
IV2 = b"iv-0002"


def random_payload(rng, alphabet, length):
    return "".join(rng.choice(alphabet) for _ in range(length))


class TestDeterminism(unittest.TestCase):
    """Same plaintext + same key + same IV => same ciphertext, every time."""

    def test_same_key_same_iv_deterministic(self):
        scheme = FieldScheme(KEY, 16, tweak=IV1)
        payload = "622202123456789"
        plain = payload + scheme.check_digit(payload)
        first = scheme.encrypt(plain)
        for _ in range(5):
            self.assertEqual(first, FieldScheme(KEY, 16, tweak=IV1).encrypt(plain))
            self.assertEqual(first, scheme.encrypt(plain))

    def test_golden_vector(self):
        # Locks cross-process / cross-run determinism.
        scheme = FieldScheme(KEY, 16, tweak=IV1)
        payload = "622202123456789"
        plain = payload + scheme.check_digit(payload)
        self.assertEqual(scheme.encrypt(plain), GOLDEN_CARD)

    def test_core_primitive_deterministic(self):
        fpe = FeistelFPE(KEY, DIGITS, 10, tweak=IV1)
        self.assertEqual(fpe.encrypt("0123456789"), fpe.encrypt("0123456789"))
        self.assertEqual(fpe.encrypt("0123456789"), GOLDEN_CORE)


class TestTweakSeparation(unittest.TestCase):
    """Different IVs => different ciphertexts, all decryptable."""

    def test_different_ivs_differ_and_roundtrip(self):
        plain_payload = "622202123456789"
        ciphertexts = set()
        for i in range(8):
            tweak = b"iv-%04d" % i
            scheme = FieldScheme(KEY, 16, tweak=tweak)
            plain = plain_payload + scheme.check_digit(plain_payload)
            ct = scheme.encrypt(plain)
            ciphertexts.add(ct)
            self.assertEqual(scheme.decrypt(ct), plain)
            self.assertTrue(scheme.is_valid(ct))
        self.assertEqual(len(ciphertexts), 8, "distinct IVs must give distinct ciphertexts")

    def test_wrong_iv_garbles_plaintext(self):
        # FPE is not authenticated encryption: any well-formed ciphertext
        # decrypts under any IV, but a wrong IV yields a different plaintext.
        scheme1 = FieldScheme(KEY, 16, tweak=IV1)
        scheme2 = FieldScheme(KEY, 16, tweak=IV2)
        payload = "622202123456789"
        plain = payload + scheme1.check_digit(payload)
        ct = scheme1.encrypt(plain)
        self.assertNotEqual(scheme2.decrypt(ct), plain)
        self.assertEqual(scheme1.decrypt(ct), plain)

    def test_different_keys_differ(self):
        payload = "622202123456789"
        s1, s2 = FieldScheme(KEY, 16, tweak=IV1), FieldScheme(KEY2, 16, tweak=IV1)
        p1 = payload + s1.check_digit(payload)
        self.assertNotEqual(s1.encrypt(p1), s2.encrypt(p1))


class TestRoundTrip(unittest.TestCase):
    """Encrypt/decrypt round trips across alphabets, lengths, IVs."""

    def test_numeric_random_roundtrips(self):
        rng = random.Random(20261004)
        for length in (2, 3, 10, 15, 16, 19):
            fpe = FeistelFPE(KEY, DIGITS, length, tweak=IV1)
            for _ in range(20):
                plain = random_payload(rng, DIGITS, length)
                self.assertEqual(fpe.decrypt(fpe.encrypt(plain)), plain)

    def test_alphanumeric_random_roundtrips(self):
        rng = random.Random(20261004)
        for length in (2, 5, 17, 20):
            fpe = FeistelFPE(KEY, ALPHANUMERIC, length, tweak=IV1)
            for _ in range(20):
                plain = random_payload(rng, ALPHANUMERIC, length)
                self.assertEqual(fpe.decrypt(fpe.encrypt(plain)), plain)

    def test_field_scheme_roundtrips_both_alphabets(self):
        rng = random.Random(7)
        for alphabet in (DIGITS, ALPHANUMERIC):
            scheme = FieldScheme(KEY, 18, alphabet=alphabet, tweak=IV1)
            for _ in range(50):
                payload = random_payload(rng, alphabet, 17)
                plain = payload + scheme.check_digit(payload)
                ct = scheme.encrypt(plain)
                self.assertEqual(scheme.decrypt(ct), plain)
                self.assertTrue(scheme.is_valid(ct))


class TestFormatPreservation(unittest.TestCase):
    """Length, charset and check digit are preserved; pass rate is reported."""

    def test_length_and_charset_preserved(self):
        rng = random.Random(1)
        for alphabet in (DIGITS, ALPHANUMERIC):
            for length in (2, 7, 16, 25):
                fpe = FeistelFPE(KEY, alphabet, length, tweak=IV1)
                for _ in range(20):
                    ct = fpe.encrypt(random_payload(rng, alphabet, length))
                    self.assertEqual(len(ct), length)
                    self.assertTrue(set(ct) <= set(alphabet))

    def test_check_digit_pass_rate(self):
        rng = random.Random(99)
        total, passed, kept_without_recompute = 0, 0, 0
        for alphabet, length in ((DIGITS, 16), (ALPHANUMERIC, 18)):
            scheme = FieldScheme(KEY, length, alphabet=alphabet, tweak=IV1)
            for _ in range(200):
                payload = random_payload(rng, alphabet, length - 1)
                plain = payload + scheme.check_digit(payload)
                ct = scheme.encrypt(plain)
                total += 1
                passed += scheme.is_valid(ct)
                # Baseline: what the pass rate would be if the check digit
                # were NOT recomputed (old check digit kept).
                stale = ct[:-1] + plain[-1]
                kept_without_recompute += scheme.is_valid(stale)
        rate = 100.0 * passed / total
        stale_rate = 100.0 * kept_without_recompute / total
        print("\n[check-digit] recomputed pass rate: %d/%d = %.1f%% "
              "(baseline without recompute: %.1f%%)"
              % (passed, total, rate, stale_rate))
        self.assertEqual(passed, total, "recomputed check digits must always validate")
        self.assertLess(stale_rate, 30.0, "baseline should be near 1/radix")

    def test_leading_zeros_preserved_as_format(self):
        fpe = FeistelFPE(KEY, DIGITS, 12, tweak=IV1)
        plain = "000000000001"
        ct = fpe.encrypt(plain)
        self.assertEqual(len(ct), 12)
        self.assertEqual(fpe.decrypt(ct), plain)


class TestBoundaries(unittest.TestCase):
    """Edge values: min length, odd length, all-min/all-max symbols, repeats."""

    def test_min_length_two(self):
        fpe = FeistelFPE(KEY, DIGITS, 2, tweak=IV1)
        for plain in ("00", "01", "99", "42"):
            self.assertEqual(fpe.decrypt(fpe.encrypt(plain)), plain)

    def test_odd_lengths(self):
        for length in (3, 5, 15, 17):
            fpe = FeistelFPE(KEY, DIGITS, length, tweak=IV1)
            plain = "9" * length
            self.assertEqual(fpe.decrypt(fpe.encrypt(plain)), plain)

    def test_all_min_and_all_max_symbols(self):
        for alphabet in (DIGITS, ALPHANUMERIC):
            fpe = FeistelFPE(KEY, alphabet, 10, tweak=IV1)
            for plain in (alphabet[0] * 10, alphabet[-1] * 10):
                ct = fpe.encrypt(plain)
                self.assertEqual(len(ct), 10)
                self.assertEqual(fpe.decrypt(ct), plain)

    def test_max_numeric_value(self):
        fpe = FeistelFPE(KEY, DIGITS, 19, tweak=IV1)  # 10**19 > 2**63
        plain = "9" * 19
        self.assertEqual(fpe.decrypt(fpe.encrypt(plain)), plain)

    def test_empty_tweak_allowed_and_distinct(self):
        f_empty = FeistelFPE(KEY, DIGITS, 10, tweak=b"")
        f_iv = FeistelFPE(KEY, DIGITS, 10, tweak=IV1)
        plain = "5555555555"
        self.assertNotEqual(f_empty.encrypt(plain), f_iv.encrypt(plain))
        self.assertEqual(f_empty.decrypt(f_empty.encrypt(plain)), plain)

    def test_permutation_on_small_domain(self):
        # Exhaustive: encryption must be a bijection on the whole domain.
        for length in (2, 3):  # 3 exercises cycle walking
            fpe = FeistelFPE(KEY, "01", length, tweak=IV1)
            domain = [bin(i)[2:].zfill(length) for i in range(2 ** length)]
            images = {fpe.encrypt(p) for p in domain}
            self.assertEqual(images, set(domain))
            for p in domain:
                self.assertEqual(fpe.decrypt(fpe.encrypt(p)), p)


class TestInvalidInputs(unittest.TestCase):
    """Illegal inputs must be rejected with clear errors."""

    def test_symbol_outside_alphabet(self):
        fpe = FeistelFPE(KEY, DIGITS, 6, tweak=IV1)
        for bad in ("12345a", "123 45", "１２３４５６"):
            with self.assertRaises(InvalidInputError):
                fpe.encrypt(bad)

    def test_wrong_length(self):
        fpe = FeistelFPE(KEY, DIGITS, 6, tweak=IV1)
        for bad in ("", "12345", "1234567"):
            with self.assertRaises(InvalidInputError):
                fpe.encrypt(bad)

    def test_non_string_input(self):
        fpe = FeistelFPE(KEY, DIGITS, 6, tweak=IV1)
        for bad in (123456, None, b"123456", ["1"] * 6):
            with self.assertRaises(InvalidInputError):
                fpe.encrypt(bad)

    def test_bad_key(self):
        for bad in (b"", b"short", "0" * 32, None, 1234):
            with self.assertRaises(InvalidKeyError):
                FeistelFPE(bad, DIGITS, 6, tweak=IV1)

    def test_bad_tweak(self):
        for bad in ("iv-0001", 123, None):
            with self.assertRaises(InvalidTweakError):
                FeistelFPE(KEY, DIGITS, 6, tweak=bad)

    def test_bad_alphabet_and_length(self):
        with self.assertRaises(InvalidInputError):
            FeistelFPE(KEY, "001", 6, tweak=IV1)  # duplicate symbols
        with self.assertRaises(InvalidInputError):
            FeistelFPE(KEY, "0", 6, tweak=IV1)     # radix < 2
        with self.assertRaises(InvalidInputError):
            FeistelFPE(KEY, DIGITS, 1, tweak=IV1)  # length < 2

    def test_invalid_check_digit_rejected(self):
        scheme = FieldScheme(KEY, 16, tweak=IV1)
        payload = "622202123456789"
        good = payload + scheme.check_digit(payload)
        bad = payload + str((int(good[-1]) + 1) % 10)
        self.assertTrue(scheme.is_valid(good))
        self.assertFalse(scheme.is_valid(bad))
        with self.assertRaises(InvalidCheckDigitError):
            scheme.encrypt(bad)

    def test_tampered_ciphertext_detected(self):
        scheme = FieldScheme(KEY, 16, tweak=IV1)
        payload = "622202123456789"
        plain = payload + scheme.check_digit(payload)
        ct = scheme.encrypt(plain)
        tampered = ct[:-1] + str((int(ct[-1]) + 1) % 10)
        with self.assertRaises(InvalidCheckDigitError):
            scheme.decrypt(tampered)


# Golden vectors: filled in by the maintainer, locked by TestDeterminism.
GOLDEN_CARD = "8335836305787158"
GOLDEN_CORE = "4564390470"


if __name__ == "__main__":
    unittest.main(verbosity=2)
