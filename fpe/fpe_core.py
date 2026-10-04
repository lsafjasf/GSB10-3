"""Format-preserving encryption (core primitive).

A balanced Feistel network over a radix domain, in the spirit of NIST
SP 800-38G (FF1), but built entirely from Python standard-library primitives:

* the round pseudo-random function is HMAC-SHA256;
* the half-domain value is produced by rejection sampling, so it is uniform
  and unbiased even when the domain size is not a power of 256;
* 10 rounds (even number, so encrypt/decrypt share the same round orientation);
* odd-length fields are supported with cycle walking (Black-Rogaway RET): the
  permutation on an even-sized enclosing domain is iterated until the result
  falls back into the true domain.

Determinism: for a fixed (key, tweak, alphabet, length), encryption of the
same plaintext always yields the same ciphertext.  The tweak plays the role
of the initial vector.
"""

import hmac
import hashlib

DIGEST = hashlib.sha256
DIGEST_LEN = DIGEST().digest_size

DIGITS = "0123456789"
ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

MIN_LEN = 2          # Feistel needs at least one symbol per half
MIN_KEY_LEN = 16     # bytes; reject obviously weak keys
NUM_ROUNDS = 10


class FPEError(ValueError):
    """Base class for all format-preserving encryption errors."""


class InvalidInputError(FPEError):
    """The plaintext/ciphertext is not valid for the configured alphabet."""


class InvalidKeyError(FPEError):
    """The encryption key is missing, too short, or not bytes."""


class InvalidTweakError(FPEError):
    """The tweak/initial vector is not bytes."""


def _u32(value):
    return value.to_bytes(4, "big")


class FeistelFPE:
    """Length- and charset-preserving permutation.

    Parameters
    ----------
    key : bytes
        Secret key, at least ``MIN_KEY_LEN`` bytes.
    alphabet : str
        Ordered sequence of allowed symbols.  All inputs must contain only
        these symbols.  Common choices: :data:`DIGITS`, :data:`ALPHANUMERIC`.
    length : int
        Fixed field length in symbols.  At least 2.
    tweak : bytes
        Initial vector / domain-separation parameter.  May be empty.  A
        different tweak produces a different, independently decryptable
        permutation.
    """

    def __init__(self, key, alphabet, length, tweak=b""):
        self.key = self._check_key(key)
        self.alphabet = self._check_alphabet(alphabet)
        self.radix = len(self.alphabet)
        self.length = self._check_length(length)
        self.tweak = self._check_tweak(tweak)
        self._index = {ch: i for i, ch in enumerate(self.alphabet)}
        self.n = self.radix ** self.length
        # Balanced split of an even-length field; odd lengths use this
        # even-sized enclosing domain plus cycle walking.
        self.feistel_len = self.length if self.length % 2 == 0 else self.length + 1
        self.half = self.feistel_len // 2
        self.mod_l = self.radix ** self.half
        self.mod_r = self.radix ** (self.feistel_len - self.half)
        self.domain_n = self.radix ** self.feistel_len

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _check_key(key):
        if not isinstance(key, (bytes, bytearray)):
            raise InvalidKeyError("key must be bytes (decode/derive your string key first)")
        if len(key) < MIN_KEY_LEN:
            raise InvalidKeyError("key must be at least %d bytes" % MIN_KEY_LEN)
        return bytes(key)

    @staticmethod
    def _check_alphabet(alphabet):
        if not isinstance(alphabet, str) or len(alphabet) < 2:
            raise InvalidInputError("alphabet must be a str of at least 2 symbols")
        if len(set(alphabet)) != len(alphabet):
            raise InvalidInputError("alphabet contains duplicate symbols")
        return alphabet

    @staticmethod
    def _check_length(length):
        if not isinstance(length, int) or length < MIN_LEN:
            raise InvalidInputError("length must be an int >= %d" % MIN_LEN)
        return length

    @staticmethod
    def _check_tweak(tweak):
        if not isinstance(tweak, (bytes, bytearray)):
            raise InvalidTweakError("tweak (initial vector) must be bytes")
        return bytes(tweak)

    # -- value / string conversion -----------------------------------------

    def _to_value(self, text):
        if not isinstance(text, str):
            raise InvalidInputError("input must be str, got %s" % type(text).__name__)
        if len(text) != self.length:
            raise InvalidInputError(
                "expected length %d, got %d (%r)" % (self.length, len(text), text)
            )
        value = 0
        for ch in text:
            idx = self._index.get(ch)
            if idx is None:
                raise InvalidInputError("symbol %r is not in alphabet %r" % (ch, self.alphabet))
            value = value * self.radix + idx
        return value

    def _to_text(self, value):
        chars = []
        for _ in range(self.length):
            value, rem = divmod(value, self.radix)
            chars.append(self.alphabet[rem])
        chars.reverse()
        return "".join(chars)

    # -- PRF / round function ----------------------------------------------

    def _prf(self, round_idx, right, mod_out):
        """Unbiased pseudo-random value in [0, mod_out) via rejection sampling."""
        # 16 blocks of HMAC-SHA256 output per draw; more than enough here.
        draw_bits = 8 * DIGEST_LEN * 16
        limit = (1 << draw_bits) - ((1 << draw_bits) % mod_out)
        alpha = self.alphabet.encode("utf-8")
        right_bytes = right.to_bytes((right.bit_length() + 7) // 8 or 1, "big")
        prefix = (
            _u32(len(self.tweak)) + self.tweak
            + _u32(len(alpha)) + alpha
            + _u32(self.feistel_len)
            + _u32(self.half)
            + _u32(round_idx)
            + _u32(len(right_bytes)) + right_bytes
        )
        for draw in range(16):  # failure probability per draw is negligible
            out = 0
            for part in range(16):
                out = (out << (8 * DIGEST_LEN)) | int.from_bytes(
                    hmac.new(self.key, prefix + _u32(draw) + _u32(part), DIGEST).digest(),
                    "big",
                )
            if out < limit:
                return out % mod_out
        raise FPEError("rejection sampling failed (should not happen)")

    def _feistel(self, block, encrypt):
        left, right = divmod(block, self.mod_r)
        if encrypt:
            rounds = range(NUM_ROUNDS)
            for round_idx in rounds:
                f = self._prf(round_idx, right, self.mod_l)
                left, right = right, (left - f) % self.mod_l
        else:
            # Inverse round of (L, R) -> (R, L - F(R)): (A, B) -> (B + F(A), A),
            # with round functions applied in reverse order.
            rounds = range(NUM_ROUNDS - 1, -1, -1)
            for round_idx in rounds:
                f = self._prf(round_idx, left, self.mod_l)
                left, right = (right + f) % self.mod_l, left
        return left * self.mod_r + right

    # -- public API ---------------------------------------------------------

    def encrypt(self, plaintext):
        value = self._to_value(plaintext)
        if self.feistel_len == self.length:
            value = self._feistel(value, True)
        else:
            # Cycle walking: RET construction on [0, domain_n).
            x = value
            for _ in range(self.domain_n):
                x = self._feistel(x, True)
                if x < self.n:
                    value = x
                    break
            else:  # pragma: no cover - permutation guarantees termination
                raise FPEError("cycle walking did not terminate")
        return self._to_text(value)

    def decrypt(self, ciphertext):
        value = self._to_value(ciphertext)
        if self.feistel_len == self.length:
            value = self._feistel(value, False)
        else:
            x = value
            for _ in range(self.domain_n):
                x = self._feistel(x, False)
                if x < self.n:
                    value = x
                    break
            else:  # pragma: no cover
                raise FPEError("cycle walking did not terminate")
        return self._to_text(value)
