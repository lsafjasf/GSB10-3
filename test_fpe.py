"""FPE 库自测：往返、格式保持、确定性、IV 差异、边界与非法输入。

运行：python3 test_fpe.py -v
"""

import random
import unittest

import fpe
from fpe import (
    ALNUM,
    DIGITS,
    HEX_LOWER,
    LOWER,
    FPECipher,
    FPEError,
    decrypt_with_luhn,
    encrypt_with_luhn,
    luhn_check_digit,
    luhn_pass_rate,
    luhn_validate,
)

KEY = b"unit-test-key-0123456789abcdef"
IV1 = b"iv-0001"
IV2 = b"iv-0002"


def make_card(payload_len=15, rng=None):
    """生成带合法 Luhn 校验位的数字串（模拟卡号/证件号）。"""
    rng = rng or random
    payload = "".join(rng.choice(DIGITS) for _ in range(payload_len))
    return payload + luhn_check_digit(payload)


class TestDeterminism(unittest.TestCase):
    """同一明文 + 相同密钥 + 相同 IV => 相同密文。"""

    def test_same_key_same_iv_same_ciphertext(self):
        pt = "6222021234567890"
        c1 = FPECipher(KEY, DIGITS, iv=IV1)
        c2 = FPECipher(KEY, DIGITS, iv=IV1)
        ct1, ct2 = c1.encrypt(pt), c2.encrypt(pt)
        self.assertEqual(ct1, ct2)  # 确定性断言
        self.assertEqual(c1.encrypt(pt), ct1)  # 同一实例重复加密也一致
        self.assertEqual(c1.decrypt(ct1), pt)

    def test_determinism_across_alphabets(self):
        for alphabet, pt in ((DIGITS, "0123456789"), (ALNUM, "Ab3Zz9Qw"), (LOWER, "hello")):
            a = FPECipher(KEY, alphabet, iv=IV1).encrypt(pt)
            b = FPECipher(KEY, alphabet, iv=IV1).encrypt(pt)
            self.assertEqual(a, b)


class TestIVBehaviour(unittest.TestCase):
    """不同 IV => 密文不同，且都能正确解密。"""

    def test_different_iv_different_ciphertext(self):
        pt = "6222021234567890"
        ct1 = FPECipher(KEY, DIGITS, iv=IV1).encrypt(pt)
        ct2 = FPECipher(KEY, DIGITS, iv=IV2).encrypt(pt)
        self.assertNotEqual(ct1, ct2)
        # 各自往返
        self.assertEqual(FPECipher(KEY, DIGITS, iv=IV1).decrypt(ct1), pt)
        self.assertEqual(FPECipher(KEY, DIGITS, iv=IV2).decrypt(ct2), pt)

    def test_many_ivs_roundtrip_and_distinct(self):
        pt = "A7" * 8
        cts = set()
        for i in range(20):
            iv = ("iv-%04d" % i).encode()
            cipher = FPECipher(KEY, ALNUM, iv=iv)
            ct = cipher.encrypt(pt)
            cts.add(ct)
            self.assertEqual(cipher.decrypt(ct), pt)
        self.assertEqual(len(cts), 20)  # 20 个 IV 得到 20 个不同密文

    def test_cross_iv_decrypt_fails_format_or_differs(self):
        # 用 IV2 解 IV1 的密文，不应得到原明文（演示 IV 是解密所必需的）
        pt = "6222021234567890"
        ct = FPECipher(KEY, DIGITS, iv=IV1).encrypt(pt)
        wrong = FPECipher(KEY, DIGITS, iv=IV2).decrypt(ct)
        self.assertNotEqual(wrong, pt)


class TestFormatPreservation(unittest.TestCase):
    """长度与字符集保持不变。"""

    def test_digits_length_and_charset(self):
        cipher = FPECipher(KEY, DIGITS, iv=IV1)
        rng = random.Random(42)
        for _ in range(200):
            n = rng.randint(2, 19)
            pt = "".join(rng.choice(DIGITS) for _ in range(n))
            ct = cipher.encrypt(pt)
            self.assertEqual(len(ct), len(pt))
            self.assertTrue(ct.isdigit(), ct)
            self.assertEqual(cipher.decrypt(ct), pt)

    def test_alpha_and_alnum_charset(self):
        rng = random.Random(7)
        for alphabet in (LOWER, ALNUM, HEX_LOWER):
            cipher = FPECipher(KEY, alphabet, iv=IV1)
            allowed = set(alphabet)
            for _ in range(100):
                n = rng.randint(2, 24)
                pt = "".join(rng.choice(alphabet) for _ in range(n))
                ct = cipher.encrypt(pt)
                self.assertEqual(len(ct), n)
                self.assertTrue(set(ct) <= allowed, ct)
                self.assertEqual(cipher.decrypt(ct), pt)

    def test_mixed_case_alnum_field(self):
        # 含字母字段：大小写混合 + 数字，整体在 ALNUM 字符表内加密
        cipher = FPECipher(KEY, ALNUM, iv=IV1)
        pt = "Ab12Cd34Ef"
        ct = cipher.encrypt(pt)
        self.assertEqual(len(ct), len(pt))
        self.assertTrue(set(ct) <= set(ALNUM))
        self.assertEqual(cipher.decrypt(ct), pt)


class TestCheckDigit(unittest.TestCase):
    """校验位重新计算 + 校验通过率。"""

    def test_luhn_known_vector(self):
        # 经典 Luhn 样例：7992739871 的校验位是 3
        self.assertEqual(luhn_check_digit("7992739871"), "3")
        self.assertTrue(luhn_validate("79927398713"))
        self.assertFalse(luhn_validate("79927398714"))

    def test_encrypt_recomputes_check_digit(self):
        cipher = FPECipher(KEY, DIGITS, iv=IV1)
        pt = make_card()
        ct = encrypt_with_luhn(cipher, pt)
        self.assertEqual(len(ct), len(pt))
        self.assertTrue(ct.isdigit())
        self.assertTrue(luhn_validate(ct))  # 密文自身通过 Luhn
        self.assertEqual(decrypt_with_luhn(cipher, ct), pt)

    def test_luhn_pass_rate_is_100_percent(self):
        cipher = FPECipher(KEY, DIGITS, iv=IV1)
        rng = random.Random(2026)
        samples = [make_card(rng=rng) for _ in range(500)]
        encrypted = [encrypt_with_luhn(cipher, s) for s in samples]
        rate = luhn_pass_rate(encrypted)
        self.assertEqual(rate, 1.0)
        print("\nLuhn 校验通过率: %.2f%% (%d/%d)"
              % (rate * 100, len(encrypted), len(encrypted)))
        # 全部可往返
        for pt, ct in zip(samples, encrypted):
            self.assertEqual(decrypt_with_luhn(cipher, ct), pt)

    def test_decrypt_rejects_bad_check_digit(self):
        cipher = FPECipher(KEY, DIGITS, iv=IV1)
        ct = encrypt_with_luhn(cipher, make_card())
        bad = ct[:-1] + str((int(ct[-1]) + 1) % 10)
        with self.assertRaises(FPEError):
            decrypt_with_luhn(cipher, bad)


class TestBoundary(unittest.TestCase):
    """边界取值：全 0、全最大、最短、单字符、奇偶长度、长串。"""

    def setUp(self):
        self.cipher = FPECipher(KEY, DIGITS, iv=IV1)

    def roundtrip(self, cipher, pt):
        ct = cipher.encrypt(pt)
        self.assertEqual(len(ct), len(pt))
        self.assertEqual(cipher.decrypt(ct), pt)
        return ct

    def test_all_zeros_and_all_nines(self):
        self.roundtrip(self.cipher, "0" * 16)
        self.roundtrip(self.cipher, "9" * 16)
        ct0 = self.cipher.encrypt("0" * 16)
        self.assertNotEqual(ct0, "0" * 16)  # 全 0 不应映射为全 0

    def test_min_length_two(self):
        self.roundtrip(self.cipher, "00")
        self.roundtrip(self.cipher, "99")

    def test_single_char(self):
        cipher = FPECipher(KEY, DIGITS, iv=IV1)
        for d in DIGITS:
            ct = self.roundtrip(cipher, d)
            self.assertIn(ct, DIGITS)
        # 单字符加密是字符表上的置换：10 个数字的密文互不相同
        self.assertEqual(len({cipher.encrypt(d) for d in DIGITS}), 10)

    def test_odd_and_even_lengths(self):
        for n in range(2, 18):
            self.roundtrip(self.cipher, "3" * n)

    def test_long_text(self):
        cipher = FPECipher(KEY, ALNUM, iv=IV1)
        self.roundtrip(cipher, "Zz9" * 100)  # 300 字符

    def test_alphabet_extremes(self):
        cipher = FPECipher(KEY, ALNUM, iv=IV1)
        self.roundtrip(cipher, ALNUM[0] * 8)   # 字符表最小字符
        self.roundtrip(cipher, ALNUM[-1] * 8)  # 字符表最大字符


class TestInvalidInput(unittest.TestCase):
    """非法输入必须抛出 FPEError（ValueError 子类）。"""

    def setUp(self):
        self.cipher = FPECipher(KEY, DIGITS, iv=IV1)

    def test_char_outside_alphabet(self):
        with self.assertRaises(FPEError):
            self.cipher.encrypt("12345a")
        with self.assertRaises(FPEError):
            self.cipher.encrypt("123 45")
        with self.assertRaises(FPEError):
            self.cipher.decrypt("12x45")

    def test_empty_and_wrong_type(self):
        with self.assertRaises(FPEError):
            self.cipher.encrypt("")
        with self.assertRaises(FPEError):
            self.cipher.encrypt(None)
        with self.assertRaises(FPEError):
            self.cipher.encrypt(12345)

    def test_bad_key(self):
        with self.assertRaises(FPEError):
            FPECipher(b"short", DIGITS)
        with self.assertRaises(FPEError):
            FPECipher(12345, DIGITS)

    def test_bad_alphabet(self):
        with self.assertRaises(FPEError):
            FPECipher(KEY, "0")
        with self.assertRaises(FPEError):
            FPECipher(KEY, "00123")  # 重复字符
        with self.assertRaises(FPEError):
            FPECipher(KEY, "")

    def test_bad_rounds_and_iv(self):
        with self.assertRaises(FPEError):
            FPECipher(KEY, DIGITS, rounds=2)
        with self.assertRaises(FPEError):
            FPECipher(KEY, DIGITS, iv=123)

    def test_bad_luhn_input(self):
        with self.assertRaises(FPEError):
            luhn_check_digit("12a4")
        with self.assertRaises(FPEError):
            luhn_check_digit("")
        with self.assertRaises(FPEError):
            encrypt_with_luhn(self.cipher, "1234567890")  # 校验位不对
        with self.assertRaises(FPEError):
            luhn_pass_rate([])


class TestRoundTripFuzz(unittest.TestCase):
    """随机往返：多字符表、多长度、多 IV。"""

    def test_fuzz_roundtrip(self):
        rng = random.Random(99)
        for alphabet in (DIGITS, LOWER, ALNUM, HEX_LOWER):
            for _ in range(50):
                cipher = FPECipher(
                    KEY, alphabet, iv=("iv-%d" % rng.randint(0, 999)).encode()
                )
                n = rng.randint(1, 32)
                pt = "".join(rng.choice(alphabet) for _ in range(n))
                ct = cipher.encrypt(pt)
                self.assertEqual(len(ct), n)
                self.assertTrue(set(ct) <= set(alphabet))
                self.assertEqual(cipher.decrypt(ct), pt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
