"""pkce 库的单元测试：正常流程、一次性使用、篡改、过期、算法与边界。"""

import re
import unittest

from pkce import (
    AuthorizationCodeStore,
    CodeRecord,
    Reason,
    VERIFIER_MAX_LEN,
    VERIFIER_MIN_LEN,
    begin_authorization,
    compute_challenge,
    generate_authorization_code,
    generate_code_verifier,
    is_valid_verifier,
)


def make_store(ttl=600, now=1_000_000.0):
    """返回 (store, 可控时钟)。测试通过 clock[0] 推进时间。"""
    clock = [now]
    store = AuthorizationCodeStore(ttl=ttl, clock=lambda: clock[0])
    return store, clock


class VerifierGenerationTest(unittest.TestCase):
    def test_default_length_and_charset(self):
        verifier = generate_code_verifier()
        self.assertTrue(VERIFIER_MIN_LEN <= len(verifier) <= VERIFIER_MAX_LEN)
        self.assertTrue(is_valid_verifier(verifier))

    def test_uniqueness(self):
        values = {generate_code_verifier() for _ in range(1000)}
        self.assertEqual(len(values), 1000)

    def test_authorization_code_uniqueness_and_length(self):
        codes = {generate_authorization_code() for _ in range(1000)}
        self.assertEqual(len(codes), 1000)
        self.assertTrue(all(len(c) == 43 for c in codes))


class NormalFlowTest(unittest.TestCase):
    def test_s256_full_flow(self):
        store, _ = make_store()
        code, verifier = begin_authorization(store, "S256")
        result = store.exchange(code, verifier)
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, Reason.OK)
        self.assertEqual(store.rejections, [])

    def test_plain_method_flow(self):
        store, _ = make_store()
        code, verifier = begin_authorization(store, "plain")
        self.assertTrue(store.exchange(code, verifier).ok)


class OneTimeUseTest(unittest.TestCase):
    def test_replay_with_correct_verifier_is_rejected(self):
        store, _ = make_store()
        code, verifier = begin_authorization(store)
        self.assertTrue(store.exchange(code, verifier).ok)

        replay = store.exchange(code, verifier)
        self.assertFalse(replay.ok)
        self.assertEqual(replay.reason, Reason.CODE_ALREADY_USED)
        # 重复兑换必须留下被拒记录
        self.assertEqual(len(store.rejections), 1)
        record = store.rejections[0]
        self.assertEqual(record.reason, Reason.CODE_ALREADY_USED)
        self.assertEqual(record.code_prefix, code[:8])

    def test_failed_attempt_consumes_code(self):
        # 即使第一次兑换失败，授权码也被消耗，不能反复试探
        store, _ = make_store()
        code, verifier = begin_authorization(store)
        bad = store.exchange(code, verifier + "x" if len(verifier) < 128 else verifier[:-1] + "x")
        self.assertFalse(bad.ok)
        retry = store.exchange(code, verifier)
        self.assertEqual(retry.reason, Reason.CODE_ALREADY_USED)


class TamperedVerifierTest(unittest.TestCase):
    def test_modified_verifier_rejected(self):
        store, _ = make_store()
        code, verifier = begin_authorization(store)
        tampered = verifier[:-1] + ("A" if verifier[-1] != "A" else "B")
        result = store.exchange(code, tampered)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, Reason.VERIFIER_MISMATCH)

    def test_verifier_from_another_session_rejected(self):
        store, _ = make_store()
        code, _ = begin_authorization(store)
        other_verifier = generate_code_verifier()
        result = store.exchange(code, other_verifier)
        self.assertEqual(result.reason, Reason.VERIFIER_MISMATCH)

    def test_missing_verifier_rejected(self):
        store, _ = make_store()
        code, _ = begin_authorization(store)
        for missing in (None, ""):
            result = store.exchange(code, missing)
            self.assertEqual(result.reason, Reason.VERIFIER_MISSING)
            # 第一次失败后码已被消耗，第二次应是 ALREADY_USED
            break
        self.assertEqual(store.rejections[0].reason, Reason.VERIFIER_MISSING)


class ExpiryTest(unittest.TestCase):
    def test_expired_code_rejected(self):
        store, clock = make_store(ttl=60)
        code, verifier = begin_authorization(store)
        clock[0] += 61  # 越过有效期
        result = store.exchange(code, verifier)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, Reason.CODE_EXPIRED)

    def test_boundary_last_valid_instant(self):
        store, clock = make_store(ttl=60)
        code, verifier = begin_authorization(store)
        clock[0] += 59.999  # 仍在有效期内
        self.assertTrue(store.exchange(code, verifier).ok)

    def test_boundary_exact_expiry_instant(self):
        store, clock = make_store(ttl=60)
        code, verifier = begin_authorization(store)
        clock[0] += 60  # 恰好到达过期时刻 -> 视为过期
        result = store.exchange(code, verifier)
        self.assertEqual(result.reason, Reason.CODE_EXPIRED)


class MethodConsistencyTest(unittest.TestCase):
    def test_unknown_method_rejected_at_issue(self):
        store, _ = make_store()
        with self.assertRaises(ValueError):
            store.issue("whatever", method="S512")

    def test_method_no_longer_allowed_rejected_at_exchange(self):
        # 授权时 plain 被允许，兑换时服务端已收紧为仅 S256 -> 算法不一致
        store, _ = make_store()
        code, verifier = begin_authorization(store, "plain")
        store.allowed_methods = ("S256",)
        result = store.exchange(code, verifier)
        self.assertEqual(result.reason, Reason.CHALLENGE_METHOD_UNSUPPORTED)

    def test_cross_algorithm_verifier_rejected(self):
        # 攻击者拿到 plain 流程的 challenge（== verifier），
        # 试图用它通过 S256 流程的兑换 -> 重算后不匹配
        store, _ = make_store()
        plain_verifier = generate_code_verifier()
        code = store.issue(compute_challenge(plain_verifier, "S256"), "S256")
        result = store.exchange(code, plain_verifier)
        self.assertTrue(result.ok)  # 正常情况先确认可用

        # 换一个码，模拟“challenge 被当成 verifier 直接提交”的混淆攻击
        store2, _ = make_store()
        leaked_challenge = compute_challenge(plain_verifier, "S256")
        code2 = store2.issue(leaked_challenge, "S256")
        # 攻击者只见过 challenge（43 字符，恰好是合法 verifier 形态）
        result2 = store2.exchange(code2, leaked_challenge)
        self.assertEqual(result2.reason, Reason.VERIFIER_MISMATCH)


class VerifierBoundaryTest(unittest.TestCase):
    def _exchange_with_verifier(self, verifier):
        store, _ = make_store()
        # 用 plain 绑定，使合法 verifier 本应通过，便于区分“格式拒绝”与“不匹配”
        code = store.issue(verifier, "plain")
        return store.exchange(code, verifier)

    def test_min_length_43_accepted(self):
        self.assertTrue(self._exchange_with_verifier("a" * 43).ok)

    def test_max_length_128_accepted(self):
        self.assertTrue(self._exchange_with_verifier("a" * 128).ok)

    def test_length_42_rejected_as_malformed(self):
        result = self._exchange_with_verifier("a" * 42)
        self.assertEqual(result.reason, Reason.VERIFIER_MALFORMED)

    def test_length_129_rejected_as_malformed(self):
        result = self._exchange_with_verifier("a" * 129)
        self.assertEqual(result.reason, Reason.VERIFIER_MALFORMED)

    def test_illegal_characters_rejected(self):
        for bad in ("a" * 42 + "+", "a" * 42 + "/", "a" * 42 + "=", "a" * 42 + " "):
            self.assertFalse(is_valid_verifier(bad))
        self.assertEqual(
            self._exchange_with_verifier("a" * 42 + "+").reason,
            Reason.VERIFIER_MALFORMED,
        )

    def test_all_unreserved_characters_accepted(self):
        verifier = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        self.assertTrue(is_valid_verifier(verifier))
        self.assertTrue(self._exchange_with_verifier(verifier).ok)


class MiscTest(unittest.TestCase):
    def test_unknown_code_rejected(self):
        store, _ = make_store()
        result = store.exchange("no-such-code", generate_code_verifier())
        self.assertEqual(result.reason, Reason.CODE_NOT_FOUND)
        self.assertEqual(store.rejections[0].reason, Reason.CODE_NOT_FOUND)

    def test_empty_or_none_code_rejected(self):
        store, _ = make_store()
        for bad in (None, ""):
            self.assertEqual(
                store.exchange(bad, generate_code_verifier()).reason,
                Reason.CODE_NOT_FOUND,
            )

    def test_challenge_is_sha256_base64url(self):
        # RFC 7636 附录 B 的官方测试向量
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        self.assertEqual(compute_challenge(verifier, "S256"), expected)

    def test_imported_record_exchange(self):
        store, clock = make_store()
        verifier = generate_code_verifier()
        record = CodeRecord(
            code="imported-code",
            challenge=compute_challenge(verifier, "S256"),
            method="S256",
            issued_at=clock[0],
            ttl=600,
        )
        store.import_record(record)
        self.assertTrue(store.exchange("imported-code", verifier).ok)

    def test_rejection_log_accumulates_distinct_reasons(self):
        store, clock = make_store(ttl=10)
        store.exchange("ghost", "v")                       # CODE_NOT_FOUND
        code, verifier = begin_authorization(store)
        store.exchange(code, None)                         # VERIFIER_MISSING（并消耗）
        store.exchange(code, verifier)                     # CODE_ALREADY_USED
        code2, verifier2 = begin_authorization(store)
        clock[0] += 11
        store.exchange(code2, verifier2)                   # CODE_EXPIRED
        reasons = [r.reason for r in store.rejections]
        self.assertEqual(
            reasons,
            [
                Reason.CODE_NOT_FOUND,
                Reason.VERIFIER_MISSING,
                Reason.CODE_ALREADY_USED,
                Reason.CODE_EXPIRED,
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
