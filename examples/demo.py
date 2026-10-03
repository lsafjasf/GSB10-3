"""演示：用脚本化假对端回放四类投递场景，输出状态转移样例与重试数据。

运行：python3 examples/demo.py
产物：docs/state_transitions.md（状态转移样例）、docs/retry_data.json（重试数据）
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from smtp_delivery import (BackoffPolicy, DeliveryPolicy, FakeClock, TlsMode,
                           deliver)
from smtp_delivery.testing import server_factory

SENDER = "alice@example.com"
RCPTS = ["bob@example.net"]
MESSAGE = ("From: alice@example.com\r\nTo: bob@example.net\r\n"
           "Subject: hello\r\n\r\nHi Bob,\r\n.this line starts with a dot\r\nbye\r\n")

DOCS = ROOT / "docs"


def success_tail():
    return [
        ("reply", "250 2.1.0 sender ok\r\n"),
        ("reply", "250 2.1.5 recipient ok\r\n"),
        ("reply", "354 end data with <CR><LF>.<CR><LF>\r\n"),
        ("reply", "250 2.0.0 queued as 9F3A\r\n"),
        ("reply", "221 2.0.0 bye\r\n"),
    ]


SCENARIOS = [
    {
        "name": "normal",
        "title": "场景一：正常投递（明文，EHLO 多行能力通告）",
        "note": "CONNECT→EHLO→MAIL→RCPT→DATA→CONTENT→QUIT→DONE，一次成功。",
        "policy": DeliveryPolicy(max_attempts=3),
        "scripts": [[
            ("reply", "220 mx.example.net ESMTP ready\r\n"),
            ("reply", "250-mx.example.net\r\n250-SIZE 10485760\r\n250 8BITMIME\r\n"),
            *success_tail(),
        ]],
    },
    {
        "name": "mid_session_tls",
        "title": "场景二：会话中途被要求换用加密通道（530 → STARTTLS）",
        "note": ("PREFER 策略下首次 STARTTLS 被 454 临时拒绝，退回明文；"
                 "MAIL FROM 收到 530「必须先 STARTTLS」后，状态机回退到 "
                 "STARTTLS 状态重新升级，TLS 后重新 EHLO，最终投递成功。"),
        "policy": DeliveryPolicy(tls=TlsMode.PREFER, max_attempts=3),
        "scripts": [[
            ("reply", "220 mx.example.net ESMTP ready\r\n"),
            ("reply", "250-mx.example.net\r\n250 STARTTLS\r\n"),
            ("reply", "454 4.7.0 TLS temporarily unavailable\r\n"),
            ("reply", "530 5.7.0 Must issue a STARTTLS command first\r\n"),
            ("reply", "220 2.0.0 ready to start TLS\r\n"),
            ("reply", "250-mx.example.net\r\n250 STARTTLS\r\n"),
            *success_tail(),
        ]],
    },
    {
        "name": "transient_retry",
        "title": "场景三：临时拒绝（450 灰名单）后退避重试成功",
        "note": ("第一次尝试在 RCPT 阶段被 450 临时拒绝，按退避策略等待 30s 后"
                 "重新建连，第二次尝试完整走通。"),
        "policy": DeliveryPolicy(max_attempts=3,
                                 backoff=BackoffPolicy(base=30, factor=2,
                                                       maximum=120, jitter=0)),
        "scripts": [
            [
                ("reply", "220 mx.example.net ESMTP ready\r\n"),
                ("reply", "250-mx.example.net\r\n250 SIZE 10485760\r\n"),
                ("reply", "250 2.1.0 sender ok\r\n"),
                ("reply", "450 4.7.1 greylisted, try again later\r\n"),
            ],
            [
                ("reply", "220 mx.example.net ESMTP ready\r\n"),
                ("reply", "250-mx.example.net\r\n250 SIZE 10485760\r\n"),
                *success_tail(),
            ],
        ],
    },
    {
        "name": "permanent_abort",
        "title": "场景四：永久拒绝（550 5.1.1 用户不存在）立刻中止",
        "note": ("RCPT 阶段收到 550 5.1.1，属于永久性拒绝：不等待、不重试，"
                 "立即中止并给出应答码与增强码作为依据。"),
        "policy": DeliveryPolicy(max_attempts=5,
                                 backoff=BackoffPolicy(base=30, jitter=0)),
        "scripts": [[
            ("reply", "220 mx.example.net ESMTP ready\r\n"),
            ("reply", "250-mx.example.net\r\n250 SIZE 10485760\r\n"),
            ("reply", "250 2.1.0 sender ok\r\n"),
            ("reply", "550 5.1.1 no such user here\r\n"),
        ]],
    },
    {
        "name": "overall_timeout",
        "title": "场景五：对端卡顿触发会话超时与整体超时",
        "note": ("单次会话预算 10s、整体预算 25s。对端每次应答都卡顿："
                 "第一次会话超时（t=12s），退避 8s 后第二次会话被整体预算"
                 "截短（t=25s 处超时），预算耗尽后停止重试，连接全部释放。"),
        "policy": DeliveryPolicy(session_timeout=10.0, overall_timeout=25.0,
                                 max_attempts=5,
                                 backoff=BackoffPolicy(base=8, jitter=0)),
        "scripts": [
            [("reply", "220 mx.example.net\r\n")] + [("stall", 4.0)] * 20,
            [("reply", "220 mx.example.net\r\n")] + [("stall", 4.0)] * 20,
        ],
    },
]


def run_scenario(spec):
    clock, made, events = FakeClock(), [], []
    result = deliver(
        server_factory(spec["scripts"], clock, made),
        sender=SENDER, recipients=RCPTS, message=MESSAGE,
        policy=spec["policy"], clock=clock, server_name="mx.example.net",
        on_event=lambda att, ev: events.append((att, ev)))
    return clock, made, events, result


def format_events(events):
    lines = ["| 尝试 | 阶段 | 对端应答 | 备注 |", "| --- | --- | --- | --- |"]
    for attempt, ev in events:
        reply = ev.reply.describe() if ev.reply is not None else ""
        note = ev.note.replace("|", "\\|")
        lines.append(f"| {attempt} | {ev.stage.value} | {reply} | {note} |")
    return "\n".join(lines)


def main():
    DOCS.mkdir(exist_ok=True)
    md = ["# 状态转移样例", "",
          "由 `python3 examples/demo.py` 自动生成；时间与对端行为均为注入的确定值。", ""]
    retry_data = {"backoff_note": "delay(attempt)=min(maximum, base*factor**(attempt-1))，"
                                  "再按 jitter 向下抖动；测试注入 rand 保证确定性。",
                  "scenarios": []}

    for spec in SCENARIOS:
        clock, made, events, result = run_scenario(spec)
        md += [f"## {spec['title']}", "", spec["note"], "",
               format_events(events), "",
               f"结果：{'成功' if result.ok else '失败'}；"
               f"尝试 {len(result.attempts)} 次；总耗时 {result.total_time:.0f}s；"
               f"连接释放：{all(s.closed for s in made)}"
               + (f"；最终错误：{result.final_error}" if result.final_error else ""),
               ""]
        retry_data["scenarios"].append({
            "name": spec["name"],
            "ok": result.ok,
            "total_time": result.total_time,
            "final_error": result.final_error,
            "attempts": [vars(a) for a in result.attempts],
        })
        print(f"[{spec['name']}] ok={result.ok} attempts={len(result.attempts)} "
              f"total={result.total_time:.0f}s")

    (DOCS / "state_transitions.md").write_text("\n".join(md), encoding="utf-8")
    (DOCS / "retry_data.json").write_text(
        json.dumps(retry_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {DOCS/'state_transitions.md'} 与 {DOCS/'retry_data.json'}")


if __name__ == "__main__":
    main()
