"""演示：用脚本化对端跑四种典型场景，打印状态转移与重试数据。

运行: python3 demo.py
"""

from smtp_delivery import (DeliveryConfig, Message, deliver)
from scripted_transport import FakeClock, ScriptedTransport, HANG

BODY = "Subject: hi\r\n\r\nhello world"


def print_transitions(result, title):
    print(f"\n=== {title} ===")
    print(f"结果: {result.status} | 尝试次数: {result.attempts} | 依据: {result.reason}")
    print("状态转移:")
    for tr in result.transitions:
        code = f" <- {tr.reply_code}" if tr.reply_code else ""
        print(f"  {tr.from_state.value:8s} -> {tr.to_state.value:8s} | {tr.event}{code}")
    if result.retry_log:
        print("重试数据:")
        for r in result.retry_log:
            print(f"  第 {r.attempt} 次失败, 退避 {r.delay}s 后重试 | {r.reason}")


def make_normal():
    return [
        (None, ["220- mx.example.org ESMTP", "220 ready"]),
        ("EHLO", ["250-PIPELINING", "250 SIZE"]),
        ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
        ("RCPT TO:<b@y.test>", ["250 2.1.5 Ok"]),
        ("DATA", ["354 end data"]),
        (".", ["250 2.0.0 queued"]),
        ("QUIT", ["221 bye"]),
    ]


def make_tls_mid():
    return [
        (None, ["220 mx ready"]),
        ("EHLO", ["250-PIPELINING", "250-STARTTLS", "250 SIZE"]),
        ("MAIL FROM:<a@x.test>", ["530 5.7.0 Must issue a STARTTLS command first"]),
        ("STARTTLS", ["220 Ready"]),
        ("EHLO", ["250-PIPELINING", "250 SIZE"]),
        ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
        ("RCPT TO:<b@y.test>", ["250 2.1.5 Ok"]),
        ("DATA", ["354 go"]),
        (".", ["250 2.0.0 queued"]),
        ("QUIT", ["221 bye"]),
    ]


def make_temp_fail():
    return [
        (None, ["220 mx ready"]),
        ("EHLO", ["250 SIZE"]),
        ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
        ("RCPT TO:<b@y.test>", ["451 4.7.1 try again later"]),
        ("QUIT", ["221 bye"]),
    ]


def make_perm_fail():
    return [
        (None, ["220 mx ready"]),
        ("EHLO", ["250 SIZE"]),
        ("MAIL FROM:<a@x.test>", ["250 2.1.0 Ok"]),
        ("RCPT TO:<b@y.test>", ["550 5.1.1 no such user"]),
        ("QUIT", ["221 bye"]),
    ]


def make_hung():
    return [(None, [HANG])]


def run_case(title, scripts, config):
    clock = FakeClock()
    holder = []

    def factory():
        tr = ScriptedTransport(scripts[len(holder)], clock)
        holder.append(tr)
        return tr

    result = deliver(factory, Message("a@x.test", ["b@y.test"], BODY),
                     config, clock.now, clock.sleep)
    print_transitions(result, title)
    print(f"资源释放: {'所有连接均已关闭' if all(t.closed for t in holder) else '存在泄漏'}")


if __name__ == "__main__":
    run_case("场景1 正常投递（含多行问候/EHLO）",
             [make_normal()],
             DeliveryConfig(session_timeout=30))
    run_case("场景2 中途被要求加密（530 -> STARTTLS）",
             [make_tls_mid()],
             DeliveryConfig(session_timeout=30))
    run_case("场景3 临时拒绝后退避重试成功（451）",
             [make_temp_fail(), make_normal()],
             DeliveryConfig(session_timeout=30, backoff_base=1.0, backoff_cap=8.0))
    run_case("场景4 永久拒绝立即中止（550）",
             [make_perm_fail()],
             DeliveryConfig(session_timeout=30, max_attempts=5))
    run_case("场景5 整体超时后释放资源并重试",
             [make_hung(), make_normal()],
             DeliveryConfig(session_timeout=10, backoff_base=1.0))
