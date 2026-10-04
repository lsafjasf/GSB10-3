"""pwdhash 自测（python -m unittest -v）。

覆盖：
1. 首次设置（哈希/校验基本性质）
2. 旧参数记录按记录内嵌参数校验（不使用全局参数）
3. 登录成功后参数就地升级（升级前后记录对比 + 升级后校验）
4. 逐用户随机盐：相同口令不同用户哈希不同
5. 记录损坏 / 字段篡改 / 空口令等边界用例
"""

import base64
import unittest

import pwdhash
from pwdhash import (
    DEFAULT_PARAMS,
    LEGACY_PARAMS,
    InvalidRecordError,
    Params,
    hash_password,
    needs_upgrade,
    verify_password,
)


class FirstSetupTests(unittest.TestCase):
    """首次设置。"""

    def test_new_hash_uses_current_params(self):
        record = hash_password("Correct Horse Battery Staple")
        algo, iters, salt, digest = pwdhash._parse_record(record)
        self.assertEqual(algo, DEFAULT_PARAMS.algorithm)
        self.assertEqual(iters, DEFAULT_PARAMS.iterations)
        self.assertEqual(len(salt), DEFAULT_PARAMS.salt_bytes)
        self.assertEqual(len(digest), DEFAULT_PARAMS.dklen)

    def test_correct_password_verifies_without_upgrade(self):
        record = hash_password("pw-12345")
        result = verify_password("pw-12345", record)
        self.assertTrue(result.valid)
        self.assertFalse(result.upgraded)
        self.assertEqual(result.record_before, record)
        self.assertEqual(result.record_after, record)

    def test_wrong_password_fails(self):
        record = hash_password("pw-12345")
        result = verify_password("PW-12345", record)
        self.assertFalse(result.valid)
        self.assertFalse(result.upgraded)
        self.assertEqual(result.record_after, record)

    def test_record_is_self_described_string(self):
        record = hash_password("pw")
        parts = record.split("$")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], "pbkdf2_sha256")
        # 盐与哈希均为合法 base64
        base64.b64decode(parts[2], validate=True)
        base64.b64decode(parts[3], validate=True)


class LegacyVerifyTests(unittest.TestCase):
    """旧参数记录：必须按记录内参数重算，而非全局当前参数。"""

    def test_legacy_record_verifies_with_record_params(self):
        old_record = hash_password("legacy-pw", LEGACY_PARAMS)
        algo, iters, salt, digest = pwdhash._parse_record(old_record)
        self.assertEqual(iters, LEGACY_PARAMS.iterations)
        self.assertEqual(len(salt), LEGACY_PARAMS.salt_bytes)

        # 即使全局 DEFAULT_PARAMS 与旧记录不同，校验仍按记录内参数成功
        self.assertNotEqual(LEGACY_PARAMS.iterations, DEFAULT_PARAMS.iterations)
        result = verify_password("legacy-pw", old_record)
        self.assertTrue(result.valid)

    def test_current_params_change_does_not_break_old_record(self):
        # 旧记录生成后，“当前策略”已演进到更高参数
        old_record = hash_password("same-pw", LEGACY_PARAMS)
        future = Params(iterations=500_000)
        result = verify_password("same-pw", old_record, current=future)
        self.assertTrue(result.valid)  # 校验依据仍是记录中的 1000 轮
        self.assertTrue(result.upgraded)
        self.assertNotEqual(result.record_after, result.record_before)

    def test_manual_rehash_with_record_params_matches(self):
        # 直接验证：用记录里的算法/迭代/盐重算，得到记录里的哈希
        password = "deterministic-check"
        record = hash_password(password, LEGACY_PARAMS)
        algo, iters, salt, digest = pwdhash._parse_record(record)
        recomputed = pwdhash._pbkdf2(
            algo, iters, salt, password.encode("utf-8"), len(digest)
        )
        self.assertEqual(recomputed, digest)


class UpgradeTests(unittest.TestCase):
    """旧参数记录登录成功后就地升级。"""

    def test_upgrade_after_successful_login(self):
        password = "upgrade-me"
        old_record = hash_password(password, LEGACY_PARAMS)

        result = verify_password(password, old_record)
        self.assertTrue(result.valid)
        self.assertTrue(result.upgraded)
        self.assertEqual(result.record_before, old_record)
        self.assertNotEqual(result.record_after, old_record)

        # 升级后记录是当前参数
        algo, iters, salt, digest = pwdhash._parse_record(result.record_after)
        self.assertEqual(algo, DEFAULT_PARAMS.algorithm)
        self.assertEqual(iters, DEFAULT_PARAMS.iterations)
        self.assertEqual(len(salt), DEFAULT_PARAMS.salt_bytes)
        self.assertEqual(len(digest), DEFAULT_PARAMS.dklen)

        # 新记录能继续校验同一口令
        again = verify_password(password, result.record_after)
        self.assertTrue(again.valid)
        self.assertFalse(again.upgraded)
        self.assertEqual(again.record_before, again.record_after)

    def test_upgrade_generates_fresh_salt(self):
        password = "fresh-salt-pw"
        old_record = hash_password(password, LEGACY_PARAMS)
        old_salt = pwdhash._parse_record(old_record)[2]
        result = verify_password(password, old_record)
        new_salt = pwdhash._parse_record(result.record_after)[2]
        self.assertNotEqual(old_salt, new_salt)

    def test_no_upgrade_on_wrong_password(self):
        record = hash_password("right-pw", LEGACY_PARAMS)
        result = verify_password("wrong-pw", record)
        self.assertFalse(result.valid)
        self.assertFalse(result.upgraded)
        self.assertEqual(result.record_after, record)

    def test_needs_upgrade_detects_each_param_axis(self):
        password = "p"
        self.assertTrue(needs_upgrade(hash_password(password, LEGACY_PARAMS)))
        self.assertFalse(needs_upgrade(hash_password(password, DEFAULT_PARAMS)))
        # 算法不同
        if "pbkdf2_sha512" in pwdhash._SUPPORTED_ALGORITHMS:
            sha512 = Params(algorithm="pbkdf2_sha512", iterations=260_000,
                            salt_bytes=16, dklen=32)
            self.assertTrue(needs_upgrade(hash_password(password, sha512)))
        # 盐长度不同
        short_salt = Params(iterations=260_000, salt_bytes=8)
        self.assertTrue(needs_upgrade(hash_password(password, short_salt)))


class RandomSaltTests(unittest.TestCase):
    """逐用户随机盐：相同口令、不同用户 -> 不同哈希。"""

    def test_same_password_different_users_differ(self):
        password = "identical-password"
        records = {user: hash_password(password)
                   for user in ("alice", "bob", "carol")}
        # 三条记录整体不同
        self.assertEqual(len(set(records.values())), 3)
        # 关键断言：盐不同（哈希也因此不同）
        salts = {pwdhash._parse_record(r)[2] for r in records.values()}
        self.assertEqual(len(salts), 3)
        hashes = {pwdhash._parse_record(r)[3] for r in records.values()}
        self.assertEqual(len(hashes), 3)

    def test_same_password_same_user_regenerated_also_differs(self):
        password = "identical-password"
        r1 = hash_password(password)
        r2 = hash_password(password)
        self.assertNotEqual(r1, r2)
        self.assertNotEqual(pwdhash._parse_record(r1)[2],
                            pwdhash._parse_record(r2)[2])
        # 但两条记录都能校验通过
        self.assertTrue(verify_password(password, r1).valid)
        self.assertTrue(verify_password(password, r2).valid)


class CorruptedRecordTests(unittest.TestCase):
    """记录损坏与边界用例。"""

    CORRUPTED = [
        "",
        "not-a-valid-record",
        "pbkdf2_sha256$abc$c2FsdA$aGFzaA",       # 迭代次数非数字
        "pbkdf2_sha256$0$c2FsdA$aGFzaA",         # 迭代次数为 0
        "pbkdf2_sha256$260000$$aGFzaA",          # 空盐
        "pbkdf2_sha256$260000$c2FsdA$",          # 空哈希
        "pbkdf2_sha256$260000$c2FsdA$@@@@",      # 非法 base64 哈希
        "pbkdf2_sha256$260000$@@@$aGFzaA",       # 非法 base64 盐
        "pbkdf2_md5$260000$c2FsdA$aGFzaA",       # 不支持的算法
        "pbkdf2_sha256$260000$c2FsdA",           # 字段缺失
        "pbkdf2_sha256$260000$c2FsdA$aGFzaA$x",  # 字段过多
        "pbkdf2_sha256$999999999999$c2FsdA$aGFzaA",  # 迭代次数越界
    ]

    def test_corrupted_records_raise_on_verify(self):
        for bad in self.CORRUPTED:
            with self.subTest(record=bad):
                with self.assertRaises(InvalidRecordError):
                    verify_password("any-pw", bad)

    def test_corrupted_records_raise_on_needs_upgrade(self):
        for bad in self.CORRUPTED:
            with self.subTest(record=bad):
                with self.assertRaises(InvalidRecordError):
                    needs_upgrade(bad)

    def test_non_string_record_raises(self):
        with self.assertRaises(InvalidRecordError):
            verify_password("pw", None)
        with self.assertRaises(InvalidRecordError):
            verify_password("pw", 12345)
        with self.assertRaises(InvalidRecordError):
            verify_password("pw", b"pbkdf2_sha256$1$c2$aG")

    def test_tampered_hash_fails_but_does_not_raise(self):
        # 结构完整但哈希被改 -> 校验失败（不是损坏异常）
        record = hash_password("pw", LEGACY_PARAMS)
        algo, iters, salt, digest = pwdhash._parse_record(record)
        tampered = bytearray(digest)
        tampered[0] ^= 0xFF
        bad_record = pwdhash._build_record(algo, iters, salt, bytes(tampered))
        result = verify_password("pw", bad_record)
        self.assertFalse(result.valid)
        self.assertFalse(result.upgraded)

    def test_password_input_validation(self):
        record = hash_password("ok-pw")
        with self.assertRaises(ValueError):
            hash_password("")
        with self.assertRaises(ValueError):
            verify_password("", record)
        with self.assertRaises(TypeError):
            hash_password(b"bytes-not-str")  # type: ignore[arg-type]

    def test_params_validation(self):
        with self.assertRaises(ValueError):
            Params(algorithm="md5").validate()
        with self.assertRaises(ValueError):
            Params(iterations=0).validate()
        with self.assertRaises(ValueError):
            Params(salt_bytes=4).validate()
        with self.assertRaises(ValueError):
            Params(dklen=8).validate()

    def test_unicode_password_is_utf8(self):
        password = "Pässwört🔒密码"
        record = hash_password(password)
        self.assertTrue(verify_password(password, record).valid)
        self.assertFalse(verify_password(password.encode("utf-8").decode("latin-1"),
                                         record).valid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
