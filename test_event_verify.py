"""event_verify 自测：正常 / 过旧 / 过新 / 重放 / 时钟回拨 / 边界。

运行: python3 -m unittest test_event_verify -v
"""

import unittest

from event_verify import (
    ERR_BAD_SIGNATURE,
    ERR_CLOCK_ROLLBACK,
    ERR_REPLAY,
    ERR_TOO_NEW,
    ERR_TOO_OLD,
    OK,
    SignatureVerifier,
    sign,
)

SECRET = b"test-secret-key"
MAX_AGE = 300        # 允许最旧 300 秒
MAX_FUTURE = 60      # 允许最多超前 60 秒
NOW = 1_700_000_000  # 固定的“当前时间”，时间完全注入


def make_verifier():
    return SignatureVerifier(
        SECRET, max_age=MAX_AGE, max_future_skew=MAX_FUTURE, clock=lambda: NOW
    )


def good_msg(ts=NOW, payload="event:order-created:42"):
    return payload, ts, sign(SECRET, payload, ts)


class TestNormal(unittest.TestCase):
    def test_valid_message_accepted(self):
        v = make_verifier()
        ok, reason = v.verify(*good_msg())
        self.assertTrue(ok)
        self.assertEqual(reason, OK)

    def test_tampered_payload_rejected(self):
        v = make_verifier()
        payload, ts, sig = good_msg()
        ok, reason = v.verify(payload + "x", ts, sig)
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_BAD_SIGNATURE)

    def test_tampered_timestamp_rejected(self):
        # 只改时间戳、沿用旧签名 => 签名不匹配，说明二者已绑定。
        v = make_verifier()
        payload, ts, sig = good_msg()
        ok, reason = v.verify(payload, ts + 1, sig)
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_BAD_SIGNATURE)

    def test_wrong_secret_rejected(self):
        v = make_verifier()
        payload, ts, _ = good_msg()
        bad_sig = sign(b"other-secret", payload, ts)
        ok, reason = v.verify(payload, ts, bad_sig)
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_BAD_SIGNATURE)


class TestWindowBoundaries(unittest.TestCase):
    """窗口: NOW-300 <= ts <= NOW+60（闭区间），两侧边界都要测。"""

    def test_lower_boundary_accepted(self):
        v = make_verifier()
        ok, _ = v.verify(*good_msg(ts=NOW - MAX_AGE))
        self.assertTrue(ok)

    def test_one_second_below_lower_boundary_rejected(self):
        v = make_verifier()
        ok, reason = v.verify(*good_msg(ts=NOW - MAX_AGE - 1))
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_TOO_OLD)

    def test_upper_boundary_accepted(self):
        v = make_verifier()
        ok, _ = v.verify(*good_msg(ts=NOW + MAX_FUTURE))
        self.assertTrue(ok)

    def test_one_second_above_upper_boundary_rejected(self):
        v = make_verifier()
        ok, reason = v.verify(*good_msg(ts=NOW + MAX_FUTURE + 1))
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_TOO_NEW)

    def test_far_future_rejected(self):
        v = make_verifier()
        ok, reason = v.verify(*good_msg(ts=NOW + 10_000))
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_TOO_NEW)


class TestReplay(unittest.TestCase):
    def test_duplicate_signature_rejected(self):
        v = make_verifier()
        payload, ts, sig = good_msg()

        duplicate_accepts = 0
        ok, _ = v.verify(payload, ts, sig)
        self.assertTrue(ok)  # 第一次接受

        for _ in range(3):  # 同一条消息重复投递
            ok, reason = v.verify(payload, ts, sig)
            if ok:
                duplicate_accepts += 1
            else:
                self.assertEqual(reason, ERR_REPLAY)

        # 重放拒绝数据：重复接受数必须为 0
        self.assertEqual(duplicate_accepts, 0)

    def test_same_payload_new_timestamp_is_new_message(self):
        # 新时间戳 => 新签名 => 不是重放，应被接受。
        v = make_verifier()
        ok1, _ = v.verify(*good_msg(ts=NOW - 10))
        ok2, _ = v.verify(*good_msg(ts=NOW))
        self.assertTrue(ok1)
        self.assertTrue(ok2)

    def test_used_signature_expires_after_window(self):
        # 超过窗口后旧签名从缓存清理（此时旧消息本就会被时间窗拒绝）。
        v = make_verifier()
        payload, ts, sig = good_msg()
        ok, _ = v.verify(payload, ts, sig)
        self.assertTrue(ok)
        later = NOW + MAX_AGE + 1
        ok, reason = v.verify(payload, ts, sig, now=later)
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_TOO_OLD)


class TestClockRollback(unittest.TestCase):
    """时钟回拨：采取保守策略——拒绝，直到时钟追平历史最高读数。

    理由：若回拨时放宽（用回拨后的时间计算窗口），攻击者可在时钟
    回拨期间重放窗口外的旧消息，使旧签名重新落入“有效窗口”。
    拒绝的代价是回拨期间暂时拒收，属于 fail-closed，安全优先。
    """

    def test_rollback_rejected(self):
        v = make_verifier()
        ok, _ = v.verify(*good_msg())
        self.assertTrue(ok)

        rolled_back = NOW - 120  # 时钟回拨 2 分钟
        ok, reason = v.verify(*good_msg(), now=rolled_back)
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_CLOCK_ROLLBACK)

    def test_rollback_does_not_reopen_window_for_old_messages(self):
        # 关键攻击场景：旧消息原本已超出窗口，时钟回拨后也不得复活。
        v = make_verifier()
        old_ts = NOW - MAX_AGE - 1
        ok, reason = v.verify(*good_msg(ts=old_ts))
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_TOO_OLD)

        rolled_back = NOW - 200  # 回拨后 old_ts 会重新落入窗口
        ok, reason = v.verify(*good_msg(ts=old_ts), now=rolled_back)
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_CLOCK_ROLLBACK)

    def test_recovery_after_clock_catches_up(self):
        v = make_verifier()
        ok, _ = v.verify(*good_msg())
        self.assertTrue(ok)
        ok, reason = v.verify(*good_msg(ts=NOW + 5), now=NOW - 10)
        self.assertFalse(ok)
        self.assertEqual(reason, ERR_CLOCK_ROLLBACK)
        # 时钟追平高水位后恢复正常。
        ok, _ = v.verify(*good_msg(ts=NOW + 5), now=NOW + 5)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
