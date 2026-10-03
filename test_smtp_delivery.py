"""smtp_delivery 自测（unittest，仅标准库）。

运行: python3 -m unittest -v test_smtp_delivery
"""

import unittest

from smtp_delivery import (DeliveryConfig, Message, State, Outcome,
                           Reply, deliver, parse_reply_line, ProtocolError)
from scripted_transport import FakeClock, ScriptedTransport, HANG

BODY = "Subject: hi\r\n\r\nhello\n.dotted line\nbye"


def normal_script(extra_caps=()):
    caps = ["250-PIPELINING", "250-8BITMIME"]
    caps += [f"250-{c}" for c in extra_caps]
    caps.append("250 SIZE 10485760")
    return [
        (None, ["220- mx.example.org ESMTP", "220 ready"]),
        ("EHLO", caps),
        ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
        ("RCPT TO:<b@y.test>", ["250 2.1.5 Ok"]),
        ("DATA", ["354 End data with <CR><LF>.<CR><LF>"]),
        (".", ["250 2.0.0 queued"]),
        ("QUIT", ["221 2.0.0 bye"]),
    ]


class NormalDeliveryTests(unittest.TestCase):
    def test_normal_delivery(self):
        t = ScriptedTransport(normal_script())
        cfg = DeliveryConfig(session_timeout=30)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, cfg, FakeClock().now).run()

        self.assertIs(result.outcome, Outcome.DELIVERED)
        states = [tr.to_state for tr in result.transitions]
        self.assertEqual(states, [State.GREETED, State.EHLO, State.MAIL,
                                  State.RCPT, State.DATA, State.DONE])
        # QUIT 已发，资源已释放
        self.assertEqual(t.sent[-1], "QUIT")
        self.assertTrue(t.closed)

    def test_dot_stuffing(self):
        t = ScriptedTransport(normal_script())
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        DeliverySession(t, msg, DeliveryConfig(), FakeClock().now).run()
        self.assertIn("..dotted line", t.sent)
        self.assertIn(".", t.sent)


class MultilineReplyTests(unittest.TestCase):
    def test_multiline_greeting_and_ehlo_fully_read(self):
        t = ScriptedTransport(normal_script(extra_caps=("STARTTLS",)))
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, DeliveryConfig(), FakeClock().now).run()
        self.assertIs(result.outcome, Outcome.DELIVERED)
        # EHLO 必须在多行问候与多行 EHLO 全部读完之后才发出
        self.assertEqual(t.sent[0], "EHLO localhost")

    def test_decision_uses_complete_reply_not_first_line(self):
        # 首行 250- 像成功，但整段应答实际是 451：必须读完再判断
        script = [
            (None, ["220 ready"]),
            ("EHLO", ["250-smtp.example.org greets you",
                      "250-feature list omitted",
                      "250 ok"]),
            ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
            ("RCPT TO:<b@y.test>",
             ["451-4.7.1 service temporarily unavailable",
              "451 4.7.1 try again later"]),
            ("QUIT", ["221 bye"]),
        ]
        t = ScriptedTransport(script)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, DeliveryConfig(), FakeClock().now).run()
        self.assertIs(result.outcome, Outcome.TEMP_FAILURE)
        self.assertIn("451", result.reason)
        self.assertIn("try again later", result.reason)

    def test_inconsistent_multiline_code_is_protocol_error(self):
        with self.assertRaises(ProtocolError):
            parse_reply_line("bad line")
        script = [
            (None, ["220 ready"]),
            ("EHLO", ["250-first", "450 final differs"]),
        ]
        t = ScriptedTransport(script)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, DeliveryConfig(), FakeClock().now).run()
        self.assertIs(result.outcome, Outcome.PROTOCOL_ERROR)
        self.assertTrue(t.closed)


class StartTlsTests(unittest.TestCase):
    def _tls_script(self):
        return [
            (None, ["220 mx ready"]),
            ("EHLO", ["250-PIPELINING", "250-STARTTLS", "250 SIZE"]),
            ("STARTTLS", ["220 2.0.0 Ready to start TLS"]),
            ("EHLO", ["250-PIPELINING", "250 SIZE"]),   # 升级后重新 EHLO
            ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
            ("RCPT TO:<b@y.test>", ["250 2.1.5 Ok"]),
            ("DATA", ["354 go"]),
            (".", ["250 2.0.0 queued"]),
            ("QUIT", ["221 bye"]),
        ]

    def test_proactive_starttls_when_required(self):
        t = ScriptedTransport(self._tls_script())
        cfg = DeliveryConfig(require_tls=True)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, cfg, FakeClock().now).run()
        self.assertIs(result.outcome, Outcome.DELIVERED)
        self.assertEqual(t.starttls_calls, 1)
        self.assertTrue(t.tls_active)
        # EHLO 出现两次（升级后必须重新握手）
        self.assertEqual([l for l in t.sent].count("EHLO localhost"), 2)

    def test_tls_demanded_mid_session(self):
        # 策略不主动加密，但 MAIL FROM 被 530 要求加密 -> 升级后重发 MAIL FROM
        script = [
            (None, ["220 mx ready"]),
            ("EHLO", ["250-PIPELINING", "250-STARTTLS", "250 SIZE"]),
            ("MAIL FROM:<a@x.test>",
             ["530 5.7.0 Must issue a STARTTLS command first"]),
            ("STARTTLS", ["220 Ready"]),
            ("EHLO", ["250-PIPELINING", "250 SIZE"]),
            ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
            ("RCPT TO:<b@y.test>", ["250 2.1.5 Ok"]),
            ("DATA", ["354 go"]),
            (".", ["250 queued"]),
            ("QUIT", ["221 bye"]),
        ]
        t = ScriptedTransport(script)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, DeliveryConfig(), FakeClock().now).run()
        self.assertIs(result.outcome, Outcome.DELIVERED)
        self.assertTrue(t.tls_active)
        # MAIL FROM 发了两次，STARTTLS 夹在中间
        self.assertEqual([l for l in t.sent].count("MAIL FROM:<a@x.test>"), 2)
        i1 = t.sent.index("MAIL FROM:<a@x.test>")
        i2 = t.sent.index("MAIL FROM:<a@x.test>", i1 + 1)
        self.assertEqual(t.sent[i1 + 1], "STARTTLS")
        self.assertLess(i1, t.sent.index("STARTTLS"))
        self.assertLess(t.sent.index("STARTTLS"), i2)

    def test_require_tls_but_unadvertised_fails_permanently(self):
        t = ScriptedTransport(normal_script())
        cfg = DeliveryConfig(require_tls=True)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, cfg, FakeClock().now).run()
        self.assertIs(result.outcome, Outcome.PERM_FAILURE)
        self.assertTrue(t.closed)


class RetryAndRejectTests(unittest.TestCase):
    def _temp_then_ok_scripts(self):
        first = [
            (None, ["220 mx ready"]),
            ("EHLO", ["250 SIZE"]),
            ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
            ("RCPT TO:<b@y.test>",
             ["451-4.7.1 temporary", "451 4.7.1 try later"]),
            ("QUIT", ["221 bye"]),
        ]
        second = normal_script()
        return [first, second]

    def test_temp_failure_retried_with_backoff_then_succeeds(self):
        scripts = self._temp_then_ok_scripts()
        transports = []

        def factory():
            tr = ScriptedTransport(scripts[len(transports)])
            transports.append(tr)
            return tr

        clock = FakeClock()
        cfg = DeliveryConfig(session_timeout=30, max_attempts=5,
                             backoff_base=1.0, backoff_cap=8.0)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        result = deliver(factory, msg, cfg, clock.now, clock.sleep)

        self.assertEqual(result.status, "delivered")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(result.retry_log), 1)
        self.assertEqual(result.retry_log[0].delay, 1.0)  # 2^(1-1)*base
        self.assertIn("451", result.retry_log[0].reason)
        self.assertEqual(clock.sleeps, [1.0])
        # 第一次连接已关闭，第二次也已关闭
        self.assertTrue(all(tr.closed for tr in transports))

    def test_permanent_failure_aborts_immediately_with_reason(self):
        script = [
            (None, ["220 mx ready"]),
            ("EHLO", ["250 SIZE"]),
            ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
            ("RCPT TO:<b@y.test>", ["550 5.1.1 no such user"]),
            ("QUIT", ["221 bye"]),
        ]
        transports = []
        clock = FakeClock()

        def factory():
            tr = ScriptedTransport(script)
            transports.append(tr)
            return tr

        cfg = DeliveryConfig(max_attempts=5, backoff_base=1.0)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        result = deliver(factory, msg, cfg, clock.now, clock.sleep)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.attempts, 1)          # 立即中止，不重试
        self.assertEqual(result.retry_log, [])
        self.assertEqual(clock.sleeps, [])
        self.assertIn("550", result.reason)
        self.assertIn("no such user", result.reason)
        self.assertTrue(transports[0].closed)

    def test_max_attempts_exhausted_with_capped_exponential_backoff(self):
        temp = [
            (None, ["220 mx ready"]),
            ("EHLO", ["250 SIZE"]),
            ("MAIL FROM:<a@x.test>", ["452 4.3.1 insufficient storage"]),
            ("QUIT", ["221 bye"]),
        ]
        transports = []
        clock = FakeClock()

        def factory():
            tr = ScriptedTransport(temp)
            transports.append(tr)
            return tr

        cfg = DeliveryConfig(session_timeout=10, max_attempts=5,
                             backoff_base=1.0, backoff_cap=8.0)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        result = deliver(factory, msg, cfg, clock.now, clock.sleep)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.attempts, 5)
        # 退避数据：1, 2, 4, 8（第 5 次前不再 sleep），8 已封顶
        self.assertEqual([r.delay for r in result.retry_log], [1.0, 2.0, 4.0, 8.0])
        self.assertEqual(clock.sleeps, [1.0, 2.0, 4.0, 8.0])
        self.assertTrue(all(tr.closed for tr in transports))


class TimeoutTests(unittest.TestCase):
    def test_session_timeout_releases_resource_then_retry_succeeds(self):
        hung = [
            (None, [HANG]),  # 220 问候阶段挂死
        ]
        scripts = [hung, normal_script()]
        transports = []
        clock = FakeClock()

        def factory():
            tr = ScriptedTransport(scripts[len(transports)], clock)
            transports.append(tr)
            return tr

        cfg = DeliveryConfig(session_timeout=10, max_attempts=3,
                             backoff_base=1.0)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        result = deliver(factory, msg, cfg, clock.now, clock.sleep)

        self.assertEqual(result.status, "delivered")
        self.assertEqual(result.attempts, 2)
        # 挂死的连接必须已关闭（释放资源），且没有尝试发 QUIT
        self.assertTrue(transports[0].closed)
        self.assertNotIn("QUIT", transports[0].sent)
        self.assertEqual(len(result.retry_log), 1)
        self.assertIn("超时", result.retry_log[0].reason)
        self.assertEqual(clock.sleeps, [1.0])

    def test_timeout_while_waiting_mid_session_aborts_and_closes(self):
        script = [
            (None, ["220 ready"]),
            ("EHLO", ["250 SIZE"]),
            ("MAIL FROM:<a@x.test>", [HANG]),  # 命令应答阶段挂死
        ]
        clock = FakeClock()
        t = ScriptedTransport(script, clock)
        msg = Message("a@x.test", ["b@y.test"], BODY)
        from smtp_delivery import DeliverySession
        result = DeliverySession(t, msg, DeliveryConfig(session_timeout=5),
                                 clock.now).run()
        self.assertIs(result.outcome, Outcome.TIMEOUT)
        self.assertTrue(t.closed)
        self.assertNotIn("QUIT", t.sent)
        self.assertEqual(result.transitions[-1].to_state, State.ABORT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
