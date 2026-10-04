"""event_auth 自测：窗口边界、重放拒绝、时钟回拨（保守策略）。

运行：python3 -m unittest -v test_event_auth
"""

import unittest

from event_auth import (
    Verifier,
    compute_signature,
    OK,
    BAD_SIGNATURE,
    TIMESTAMP_TOO_OLD,
    TIMESTAMP_TOO_FAR_IN_FUTURE,
    REPLAY_DETECTED,
    INVALID_TIMESTAMP,
)

SECRET = b"shared-secret-for-test"
WINDOW = 300          # 过去方向允许 300s
FUTURE_SKEW = 60      # 未来方向允许 60s


class FakeClock:
    """可注入/可回拨的假时钟。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = value

    def advance(self, delta: float) -> None:
        self.value += delta

    def rollback(self, delta: float) -> None:
        self.value -= delta


def signed_msg(clock: FakeClock, ts: int, body: str = '{"event":"deposit","id":"e1"}'):
    return ts, body, compute_signature(SECRET, ts, body)


class EventAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock(1_000_000.0)
        self.verifier = Verifier(
            SECRET,
            window_seconds=WINDOW,
            future_skew_seconds=FUTURE_SKEW,
            clock=self.clock,
        )

    # ---------- 正常消息 ----------
    def test_normal_message_accepted(self) -> None:
        now = int(self.clock())
        result = self.verifier.verify(*signed_msg(self.clock, now))
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.reason, OK)

    def test_tampered_body_rejected(self) -> None:
        now = int(self.clock())
        ts, _body, sig = signed_msg(self.clock, now)
        result = self.verifier.verify(ts, '{"event":"tampered"}', sig)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, BAD_SIGNATURE)

    def test_wrong_secret_rejected(self) -> None:
        now = int(self.clock())
        other = compute_signature(b"attacker", now, '{"event":"x"}')
        result = self.verifier.verify(now, '{"event":"x"}', other)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, BAD_SIGNATURE)

    def test_invalid_timestamp_rejected(self) -> None:
        bad_values = ["1000000", None, 1.5, True]
        for bad in bad_values:
            with self.subTest(bad=bad):
                result = self.verifier.verify(bad, "body", "sig")  # type: ignore[arg-type]
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, INVALID_TIMESTAMP)

    # ---------- 时间戳过旧：窗口边界两侧 ----------
    def test_old_boundary_inside_window_accepted(self) -> None:
        now = int(self.clock())
        # 恰好落在下边界（边界含端点）：接受
        ts = now - WINDOW
        result = self.verifier.verify(*signed_msg(self.clock, ts))
        self.assertTrue(result.ok, result.reason)

    def test_old_boundary_one_second_outside_rejected(self) -> None:
        now = int(self.clock())
        # 越过下边界 1 秒：拒绝
        ts = now - WINDOW - 1
        result = self.verifier.verify(*signed_msg(self.clock, ts))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, TIMESTAMP_TOO_OLD)

    def test_future_boundary_inside_skew_accepted(self) -> None:
        now = int(self.clock())
        # 恰好落在上边界：接受
        ts = now + FUTURE_SKEW
        result = self.verifier.verify(*signed_msg(self.clock, ts))
        self.assertTrue(result.ok, result.reason)

    def test_future_boundary_one_second_outside_rejected(self) -> None:
        now = int(self.clock())
        # 越过上边界 1 秒：拒绝
        ts = now + FUTURE_SKEW + 1
        result = self.verifier.verify(*signed_msg(self.clock, ts))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, TIMESTAMP_TOO_FAR_IN_FUTURE)

    # ---------- 签名重放：重复接受数必须为 0 ----------
    def test_replay_second_copy_rejected(self) -> None:
        now = int(self.clock())
        message = signed_msg(self.clock, now)

        first = self.verifier.verify(*message)
        self.assertTrue(first.ok)

        second = self.verifier.verify(*message)
        self.assertFalse(second.ok)
        self.assertEqual(second.reason, REPLAY_DETECTED)

    def test_replay_duplicate_accept_count_is_zero(self) -> None:
        """同一条旧消息重放 100 次，重复接受次数必须为 0。"""
        now = int(self.clock())
        message = signed_msg(self.clock, now)
        self.assertTrue(self.verifier.verify(*message).ok)

        duplicate_accepts = 0
        replay_rejections = 0
        for _ in range(100):
            result = self.verifier.verify(*message)
            if result.ok:
                duplicate_accepts += 1
            elif result.reason == REPLAY_DETECTED:
                replay_rejections += 1

        self.assertEqual(duplicate_accepts, 0, "重复消息被接受，重放防护失效")
        self.assertEqual(replay_rejections, 100)
        self.assertEqual(self.verifier.tracked_signatures, 1)

    def test_old_replay_rejected_even_as_time_passes(self) -> None:
        """窗口过后重放：先过期被缓存清除，也必须被时间窗口拒绝。"""
        ts = int(self.clock())
        message = signed_msg(self.clock, ts)
        self.assertTrue(self.verifier.verify(*message).ok)

        # 时间前进到窗口 + 偏差之外，缓存条目被清除
        self.clock.advance(WINDOW + FUTURE_SKEW + 10)
        self.assertEqual(self.verifier.tracked_signatures, 0)

        result = self.verifier.verify(*message)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, TIMESTAMP_TOO_OLD)

    # ---------- 时钟回拨：保守拒绝 ----------
    def test_clock_rollback_conservative_reject(self) -> None:
        """时钟回拨后，按回拨时钟新签发的消息必须拒绝，而不是放宽窗口。"""
        t = int(self.clock())
        # T 时刻正常接受一条消息
        self.assertTrue(self.verifier.verify(*signed_msg(self.clock, t)).ok)

        # 时钟突然回拨 1000s
        self.clock.rollback(1000)
        rolled_back_now = int(self.clock())

        # 发送方按回拨后的真实时钟签署新消息，仍须拒绝（effective_now 未回退）
        result = self.verifier.verify(
            *signed_msg(self.clock, rolled_back_now, body='{"event":"new-after-rollback"}')
        )
        self.assertFalse(result.ok, "时钟回拨时必须保守拒绝")
        self.assertEqual(result.reason, TIMESTAMP_TOO_OLD)

    def test_clock_rollback_does_not_reopen_window_for_replay(self) -> None:
        """回拨不得让已接受的旧消息重新落入窗口；重放依旧被拦截。"""
        t = int(self.clock())
        old_message = signed_msg(self.clock, t, body='{"event":"old"}')
        self.assertTrue(self.verifier.verify(*old_message).ok)

        self.clock.rollback(1000)
        # 同一签名再次提交：重放缓存仍然生效
        result = self.verifier.verify(*old_message)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REPLAY_DETECTED)

    def test_clock_resumes_after_catching_up(self) -> None:
        """时钟恢复到回拨前的水平后，新消息应能正常接受（系统可自愈）。"""
        t = int(self.clock())
        self.assertTrue(self.verifier.verify(*signed_msg(self.clock, t)).ok)

        self.clock.rollback(1000)
        rolled_ts = int(self.clock())
        self.assertFalse(
            self.verifier.verify(*signed_msg(self.clock, rolled_ts)).ok
        )

        # NTP 把时钟追平并继续向前
        self.clock.set(float(t + 5))
        self.assertTrue(
            self.verifier.verify(*signed_msg(self.clock, t + 5)).ok
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
