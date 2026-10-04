"""Field-level format-preserving encryption with check digits.

The check digit is handled *outside* the Feistel permutation: the payload
(everything except the last symbol) is encrypted, then the check digit is
recomputed over the ciphertext payload.  This keeps length, charset and the
checksum invariant intact, so databases and downstream systems need no
changes.
"""

from .fpe_core import (
    ALPHANUMERIC,
    DIGITS,
    FeistelFPE,
    FPEError,
    InvalidInputError,
)


class InvalidCheckDigitError(FPEError):
    """The check digit of the supplied value does not match."""


def luhn_check_digit(payload):
    """Luhn check digit for a string of decimal digits (payload without it)."""
    total = 0
    for pos, ch in enumerate(reversed(payload)):
        digit = ord(ch) - ord("0")
        if pos % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((-total) % 10)


def weighted_check_digit(payload, alphabet):
    """Mod-radix weighted checksum over an arbitrary alphabet.

    Weight of position i (from the right, 0-based) is (i + 1) mod radix.
    The check symbol satisfies sum(value_i * weight_i) + check == 0 (mod radix).
    """
    radix = len(alphabet)
    index = {ch: i for i, ch in enumerate(alphabet)}
    total = 0
    for pos, ch in enumerate(reversed(payload)):
        total += index[ch] * ((pos % radix) + 1)
    return alphabet[(-total) % radix]


class FieldScheme:
    """Encrypt/decrypt a fixed-format field end to end.

    Parameters
    ----------
    key : bytes
        Secret key (>= 16 bytes).
    length : int
        Total field length, *including* the check digit when ``check`` is
        enabled.  Must be >= 3 with a check digit, >= 2 without.
    alphabet : str
        Allowed symbols (default: decimal digits).
    check : bool
        Whether the last symbol is a check digit.  For decimal digits the
        Luhn algorithm is used; otherwise a mod-radix weighted checksum.
    tweak : bytes
        Initial vector.
    """

    def __init__(self, key, length, alphabet=DIGITS, check=True, tweak=b""):
        self.alphabet = alphabet
        self.check = bool(check)
        self.length = length
        payload_len = length - 1 if self.check else length
        self._fpe = FeistelFPE(key, alphabet, payload_len, tweak)

    # -- check digit helpers ------------------------------------------------

    def check_digit(self, payload):
        if self.alphabet == DIGITS:
            return luhn_check_digit(payload)
        return weighted_check_digit(payload, self.alphabet)

    def is_valid(self, value):
        """True if value has the right length/charset and a valid check digit."""
        if not isinstance(value, str) or len(value) != self.length:
            return False
        if any(ch not in self.alphabet for ch in value):
            return False
        if not self.check:
            return True
        return self.check_digit(value[:-1]) == value[-1]

    # -- encrypt / decrypt ----------------------------------------------------

    def encrypt(self, plaintext, verify=True):
        if verify and self.check and not self.is_valid(plaintext):
            raise InvalidCheckDigitError("invalid check digit in %r" % (plaintext,))
        if not isinstance(plaintext, str) or len(plaintext) != self.length:
            raise InvalidInputError(
                "expected a %d-symbol string" % self.length
            )
        if self.check:
            payload = self._fpe.encrypt(plaintext[:-1])
            return payload + self.check_digit(payload)
        return self._fpe.encrypt(plaintext)

    def decrypt(self, ciphertext, verify=True):
        if not isinstance(ciphertext, str) or len(ciphertext) != self.length:
            raise InvalidInputError(
                "expected a %d-symbol string" % self.length
            )
        # A ciphertext produced by encrypt() always carries a valid check
        # digit, so an invalid one means the value was tampered with.
        if verify and self.check and not self.is_valid(ciphertext):
            raise InvalidCheckDigitError(
                "ciphertext check digit invalid (tampered value?)"
            )
        if self.check:
            payload = self._fpe.decrypt(ciphertext[:-1])
            return payload + self.check_digit(payload)
        return self._fpe.decrypt(ciphertext)


def demo():
    key = b"0123456789abcdef0123456789abcdef"
    tweak = b"iv-2026-10"
    card = FieldScheme(key, 16, tweak=tweak)
    id_no = FieldScheme(key, 18, alphabet=ALPHANUMERIC, tweak=tweak)

    plain_card = "622202123456789"
    plain_card += card.check_digit(plain_card)
    ct_card = card.encrypt(plain_card)
    print("card   plain :", plain_card, " valid:", card.is_valid(plain_card))
    print("card   cipher:", ct_card, " valid:", card.is_valid(ct_card))
    print("card   back  :", card.decrypt(ct_card))

    plain_id = "31011019900101123"
    plain_id += id_no.check_digit(plain_id)
    ct_id = id_no.encrypt(plain_id)
    print("id     plain :", plain_id, " valid:", id_no.is_valid(plain_id))
    print("id     cipher:", ct_id, " valid:", id_no.is_valid(ct_id))
    print("id     back  :", id_no.decrypt(ct_id))


if __name__ == "__main__":
    demo()
