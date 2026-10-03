"""投递会话状态机自测：脚本化假对端 + 假时钟，全部确定性、不碰网络。

运行：python3 -m unittest discover -s tests -v
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from smtp_delivery import (BackoffPolicy, DeliveryPolicy, FakeClock, TlsMode,
                           deliver)
from smtp_delivery.errors import (ConnectionLost, ProtocolError)
from smtp_delivery.replies import read_reply
from smtp_delivery.session import Stage
from smtp_delivery.testing import server_factory

SENDER = "alice@example.com"
RCPTS = ["bob@example.net"]
MESSAGE = "From: alice@example.com\r\nTo: bob@example.net\r\nSubject: hi\r\n\r\nhello\r\n"


def success_script(ehlo="250-mx\r\n250 SIZE 10485760\r\n"):
    """一次干净利落的明文投递。"""
    return [
        ("reply", "220 mx.example.net ESMTP ready\r\n"),
        ("reply", ehlo),                              # EHLO
        ("reply", "250 2.1.0 sender ok\r\n"),         # MAIL FROM
        ("reply", "250 2.1.5 recipient ok\r\n"),      # RCPT TO
        ("reply", "354 end data with <CR><LF>.<CR><LF>\r\n"),
        ("reply", "250 2.0.0 queued as 9F3A\r\n"),    # 信体
        ("reply", "221 2.0.0 bye\r\n"),               # QUIT
    ]


def reply_of(text):
    chunks = iter(text.encode().splitlines(keepends=True))
    return read_reply(lambda: next(chunks, b""))


def run_deliver(scripts, clock, made, **kwargs):
    kwargs.setdefault("sender", SENDER)
    kwargs.setdefault("recipients", RCPTS)
    kwargs.setdefault("message", MESSAGE)
    kwargs.setdefault("clock", clock)
    return deliver(server_factory(scripts, clock, made), **kwargs)


class TestReplyParsing(unittest.TestCase):
    """多行应答：必须读完整段再判断。"""

    def test_multiline_reply_parsed_fully(self):
        reply = reply_of("250-mx.example.net\r\n250-SIZE 1024\r\n250 STARTTLS\r\n")
        self.assertEqual(reply.code, 250)
        self.assertEqual(reply.lines, ["mx.example.net", "SIZE 1024", "STARTTLS"])

    def test_bare_code_final_line(self):
        reply = reply_of("250\r\n")
        self.assertEqual((reply.code, reply.lines), (250, [""]))

    def test_enhanced_status_code(self):
        reply = reply_of("550 5.1.1 no such user\r\n")
        self.assertEqual(reply.enhanced, (5, 1, 1))
        self.assertIn("5.1.1", reply.describe())

    def test_inconsistent_multiline_code_rejected(self):
        with self.assertRaises(ProtocolError):
            reply_of("250-first\r\n251 second\r\n")

    def test_malformed_lines_rejected(self):
        for bad in ("garbage\r\n", "25x bad\r\n", "250!bad\r\n", "ok\r\n"):
            with self.assertRaises(ProtocolError, msg=bad):
                reply_of(bad)

    def test_connection_lost_mid_reply(self):
        chunks = iter([b"250-partial\r\n", b""])
        with self.assertRaises(ConnectionLost):
            read_reply(lambda: next(chunks, b""))


class TestHappyPath(unittest.TestCase):
    def test_plain_delivery_and_event_trace(self):
        clock, made, events = FakeClock(), [], []
        result = run_deliver(
            [success_script()], clock, made,
            policy=DeliveryPolicy(max_attempts=3),
            on_event=lambda att, ev: events.append((att, ev.stage)))

        self.assertTrue(result.ok, result.final_error)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].outcome, "delivered")

        server = made[0]
        self.assertTrue(server.closed, "会话结束后必须释放连接")
        text = server.sent_text()
        for expected in ("EHLO mailer.local\r\n",
                         f"MAIL FROM:<{SENDER}>\r\n",
                         f"RCPT TO:<{RCPTS[0]}>\r\n",
                         "DATA\r\n", "\r\n.\r\n", "QUIT\r\n"):
            self.assertIn(expected, text)

        stages = [stage for _, stage in events]
        ordered = [s for i, s in enumerate(stages) if i == 0 or s != stages[i - 1]]
        self.assertEqual(ordered, [Stage.CONNECT, Stage.EHLO, Stage.MAIL,
                                   Stage.RCPT, Stage.DATA, Stage.CONTENT,
                                   Stage.QUIT, Stage.DONE])


class TestStarttls(unittest.TestCase):
    def test_require_tls_reads_full_multiline_ehlo(self):
        """STARTTLS 出现在 EHLO 多行应答的最后一行：只看第一行就会漏判。"""
        clock, made = FakeClock(), []
        script = [
            ("reply", "220 mx ready\r\n"),
            ("reply", "250-mx\r\n250-SIZE 1024\r\n250 STARTTLS\r\n"),
            ("reply", "220 2.0.0 ready to start TLS\r\n"),
            ("reply", "250-mx\r\n250 STARTTLS\r\n"),   # TLS 后重新 EHLO
            ("reply", "250 2.1.0 ok\r\n"),
            ("reply", "250 2.1.5 ok\r\n"),
            ("reply", "354 go\r\n"),
            ("reply", "250 2.0.0 queued\r\n"),
            ("reply", "221 bye\r\n"),
        ]
        result = run_deliver([script], clock, made,
                             policy=DeliveryPolicy(tls=TlsMode.REQUIRE))
        self.assertTrue(result.ok, result.final_error)
        self.assertTrue(made[0].tls_started)

    def test_mid_session_starttls_after_530(self):
        """PREFER 下 STARTTLS 临时失败退回明文，MAIL 被 530 拒绝后中途再升级。"""
        clock, made = FakeClock(), []
        script = [
            ("reply", "220 mx ready\r\n"),
            ("reply", "250-mx\r\n250 STARTTLS\r\n"),
            ("reply", "454 4.7.0 TLS temporarily unavailable\r\n"),
            ("reply", "530 5.7.0 Must issue a STARTTLS command first\r\n"),
            ("reply", "220 2.0.0 ready to start TLS\r\n"),
            ("reply", "250-mx\r\n250 STARTTLS\r\n"),
            ("reply", "250 2.1.0 ok\r\n"),
            ("reply", "250 2.1.5 ok\r\n"),
            ("reply", "354 go\r\n"),
            ("reply", "250 2.0.0 queued\r\n"),
            ("reply", "221 bye\r\n"),
        ]
        result = run_deliver([script], clock, made,
                             policy=DeliveryPolicy(tls=TlsMode.PREFER))
        self.assertTrue(result.ok, result.final_error)
        self.assertTrue(made[0].tls_started)
        self.assertEqual(made[0].sent_text().count("STARTTLS\r\n"), 2)

    def test_require_tls_but_not_offered_is_transient(self):
        clock, made = FakeClock(), []
        script = [("reply", "220 mx\r\n"), ("reply", "250 mx\r\n")]
        result = run_deliver(
            [script, list(script)], clock, made,
            policy=DeliveryPolicy(tls=TlsMode.REQUIRE, max_attempts=2,
                                  backoff=BackoffPolicy(base=5, jitter=0)))
        self.assertFalse(result.ok)
        self.assertEqual([a.outcome for a in result.attempts],
                         ["transient", "transient"])
        self.assertEqual(result.attempts[0].wait_after, 5.0)

    def test_530_with_tls_off_aborts_permanently(self):
        clock, made = FakeClock(), []
        script = [
            ("reply", "220 mx\r\n"),
            ("reply", "250-mx\r\n250 STARTTLS\r\n"),
            ("reply", "530 5.7.0 Must issue a STARTTLS command first\r\n"),
        ]
        result = run_deliver([script], clock, made,
                             policy=DeliveryPolicy(tls=TlsMode.OFF))
        self.assertFalse(result.ok)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].outcome, "permanent")


class TestRetryAndAbort(unittest.TestCase):
    def test_transient_rejection_then_success(self):
        clock, made = FakeClock(), []
        attempt1 = [
            ("reply", "220 mx\r\n"),
            ("reply", "250 mx\r\n"),
            ("reply", "250 2.1.0 ok\r\n"),
            ("reply", "450 4.7.1 greylisted, try later\r\n"),
        ]
        result = run_deliver(
            [attempt1, success_script()], clock, made,
            policy=DeliveryPolicy(max_attempts=3,
                                  backoff=BackoffPolicy(base=30, jitter=0)))
        self.assertTrue(result.ok, result.final_error)
        self.assertEqual(len(result.attempts), 2)
        first = result.attempts[0]
        self.assertEqual((first.outcome, first.stage, first.wait_after),
                         ("transient", "RCPT", 30.0))
        self.assertIn("450", first.detail)
        self.assertEqual(clock.now, 1_000.0 + 30.0, "退避等待必须真实推进时钟")
        self.assertTrue(all(s.closed for s in made), "每次尝试的连接都要释放")

    def test_permanent_rejection_aborts_immediately(self):
        clock, made = FakeClock(), []
        attempt1 = [
            ("reply", "220 mx\r\n"),
            ("reply", "250 mx\r\n"),
            ("reply", "250 2.1.0 ok\r\n"),
            ("reply", "550 5.1.1 no such user here\r\n"),
        ]
        result = run_deliver(
            [attempt1], clock, made,
            policy=DeliveryPolicy(max_attempts=5,
                                  backoff=BackoffPolicy(base=30, jitter=0)))
        self.assertFalse(result.ok)
        self.assertEqual(len(result.attempts), 1, "永久拒绝不得消耗重试次数")
        self.assertEqual(result.attempts[0].outcome, "permanent")
        self.assertEqual(result.attempts[0].stage, "RCPT")
        self.assertIn("550", result.final_error)
        self.assertIn("5.1.1", result.final_error, "中止必须说明依据（含增强码）")
        self.assertEqual(clock.now, 1_000.0, "永久拒绝后不得做退避等待")
        self.assertTrue(made[0].closed)

    def test_permanent_rejection_of_content(self):
        clock, made = FakeClock(), []
        script = success_script()
        script[5] = ("reply", "554 5.5.0 no storage, message refused\r\n")
        result = run_deliver([script], clock, made,
                             policy=DeliveryPolicy(max_attempts=3))
        self.assertFalse(result.ok)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].stage, "CONTENT")

    def test_greeting_421_retried_then_success(self):
        clock, made = FakeClock(), []
        busy = [("reply", "421 4.3.2 service not available, closing\r\n")]
        result = run_deliver(
            [busy, success_script()], clock, made,
            policy=DeliveryPolicy(max_attempts=3,
                                  backoff=BackoffPolicy(base=10, jitter=0)))
        self.assertTrue(result.ok, result.final_error)
        self.assertEqual(result.attempts[0].stage, "CONNECT")
        self.assertEqual(result.attempts[0].wait_after, 10.0)

    def test_greeting_554_aborts_permanently(self):
        clock, made = FakeClock(), []
        refused = [("reply", "554 5.7.1 relay access denied\r\n")]
        result = run_deliver([refused], clock, made,
                             policy=DeliveryPolicy(max_attempts=5))
        self.assertFalse(result.ok)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].outcome, "permanent")

    def test_connection_lost_mid_reply_is_retried(self):
        clock, made = FakeClock(), []
        dropped = [("reply", "220 mx\r\n"),
                   ("reply", "250-partial\r\n"),
                   ("close",)]
        result = run_deliver(
            [dropped, success_script()], clock, made,
            policy=DeliveryPolicy(max_attempts=3,
                                  backoff=BackoffPolicy(base=1, jitter=0)))
        self.assertTrue(result.ok, result.final_error)
        self.assertEqual(result.attempts[0].outcome, "transient")
        self.assertEqual(result.attempts[0].stage, "EHLO")

    def test_helo_fallback(self):
        clock, made = FakeClock(), []
        script = [
            ("reply", "220 old-server\r\n"),
            ("reply", "500 5.5.2 command unrecognized\r\n"),   # EHLO
            ("reply", "250 old-server\r\n"),                   # HELO
            ("reply", "250 ok\r\n"), ("reply", "250 ok\r\n"),
            ("reply", "354 go\r\n"), ("reply", "250 queued\r\n"),
            ("reply", "221 bye\r\n"),
        ]
        result = run_deliver([script], clock, made)
        self.assertTrue(result.ok, result.final_error)
        self.assertIn("HELO mailer.local\r\n", made[0].sent_text())


class TestTimeouts(unittest.TestCase):
    def test_session_timeout_releases_resources(self):
        clock, made = FakeClock(), []
        stalling = [("reply", "220 mx\r\n"), ("stall", 60.0)]
        result = run_deliver(
            [stalling], clock, made,
            policy=DeliveryPolicy(session_timeout=10.0, max_attempts=1))
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts[0].outcome, "transient")
        self.assertEqual(result.attempts[0].stage, "EHLO")
        self.assertIn("超时", result.attempts[0].detail)
        self.assertTrue(made[0].closed, "超时后必须释放连接资源")

    def test_overall_timeout_stops_retrying(self):
        clock, made = FakeClock(), []
        stall = [("reply", "220 mx\r\n")] + [("stall", 4.0)] * 20
        result = run_deliver(
            [list(stall), list(stall), list(stall)], clock, made,
            policy=DeliveryPolicy(session_timeout=10.0, overall_timeout=25.0,
                                  max_attempts=5,
                                  backoff=BackoffPolicy(base=8, jitter=0)))
        self.assertFalse(result.ok)
        outcomes = [a.outcome for a in result.attempts]
        self.assertEqual(outcomes, ["transient", "transient", "timeout"])
        self.assertIn("整体超时", result.final_error)
        self.assertEqual(len(made), 2, "预算耗尽后不得再发起新连接")
        self.assertTrue(all(s.closed for s in made), "所有会话资源必须释放")


class TestContentFraming(unittest.TestCase):
    def test_dot_stuffing_and_terminator(self):
        clock, made = FakeClock(), []
        message = "first\r\n.hidden\r\n..two dots\r\n.\r\nlast line no newline"
        result = run_deliver([success_script()], clock, made, message=message)
        self.assertTrue(result.ok, result.final_error)

        sent = made[0].sent
        content = sent[sent.index(b"DATA\r\n") + 1]
        self.assertIn(b"\r\n..hidden\r\n", content)
        self.assertIn(b"\r\n...two dots\r\n", content)
        self.assertIn(b"\r\n..\r\n", content, "单独一个点的行必须填充为两个点")
        self.assertTrue(content.endswith(b"last line no newline\r\n.\r\n"))
        self.assertEqual(content.count(b"\r\n.\r\n"), 1,
                         "信体中不得出现会提前结束 DATA 的孤立点行")


class TestBackoff(unittest.TestCase):
    def test_exponential_with_cap(self):
        backoff = BackoffPolicy(base=2, factor=3, maximum=10, jitter=0)
        self.assertEqual([backoff.delay(i) for i in (1, 2, 3, 4)],
                         [2.0, 6.0, 10.0, 10.0])

    def test_jitter_bounds_with_injected_rand(self):
        backoff = BackoffPolicy(base=10, jitter=0.5)
        self.assertEqual(backoff.delay(1, rand=lambda: 0.0), 5.0)
        self.assertEqual(backoff.delay(1, rand=lambda: 1.0), 10.0)


if __name__ == "__main__":
    unittest.main()
