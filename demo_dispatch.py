"""记录类型分发样例: 构造一段混合记录流, 演示按类型分发与跨记录重组。

场景(刻意制造):
  1. ClientHello 被切成 3 条握手记录, 中间还插进一条告警;
  2. 一条告警记录里装两个告警, 其中一个是协议未定义的原始码值;
  3. ChangeCipherSpec + 应用数据;
  4. 一条声明长度超缓冲区的记录, 演示“还缺多少字节”的报告。

运行: python3 demo_dispatch.py
"""

from tlsrecord import (
    Alert,
    AppData,
    ChangeCipherSpec,
    HandshakeMessage,
    NeedMoreDataError,
    RecordDispatcher,
    RecordParser,
    build_handshake,
    build_record,
    parse_record,
)

VERSION = 0x0303


def build_stream() -> bytes:
    client_hello = build_handshake(1, b"client-hello-body-payload")
    server_hello = build_handshake(2, b"server-hello")
    return b"".join(
        [
            build_record(22, VERSION, client_hello[:10]),          # 握手碎片 1
            build_record(21, VERSION, bytes([1, 90])),             # 警告: user_canceled
            build_record(22, VERSION, client_hello[10:13]),        # 握手碎片 2
            build_record(22, VERSION, client_hello[13:] + server_hello),
            build_record(21, VERSION, bytes([0xEE, 0x42, 2, 40])), # 未知码 + handshake_failure
            build_record(20, VERSION, b"\x01"),                    # CCS
            build_record(23, VERSION, b"GET / HTTP/1.1\r\n\r\n"),  # 应用数据
        ]
    )


def main() -> None:
    stream = build_stream()
    parser = RecordParser()
    dispatcher = RecordDispatcher()

    print("== 逐字节喂入, 按事件到达顺序打印 ==")
    for i in range(0, len(stream), 7):  # 每次只喂 7 字节, 模拟网络分包
        for record in parser.feed(stream[i : i + 7]):
            for event in dispatcher.dispatch(record):
                if isinstance(event, HandshakeMessage):
                    print(f"  [握手] {event.type_name} body={event.body!r}")
                elif isinstance(event, Alert):
                    print(f"  [告警] level={event.level}({event.level_name}) "
                          f"desc={event.description}({event.description_name})")
                elif isinstance(event, AppData):
                    print(f"  [应用数据] {event.data!r}")
                elif isinstance(event, ChangeCipherSpec):
                    print(f"  [CCS] fragment={event.fragment.hex()}")

    print("\n== 分发归类结果 ==")
    print(f"  握手消息: {[m.type_name for m in dispatcher.handshakes]}")
    print(f"  告警    : {[(a.level, a.description) for a in dispatcher.alerts]}")
    print(f"  应用数据: {[a.data for a in dispatcher.app_data]}")
    print(f"  CCS     : {[c.fragment.hex() for c in dispatcher.change_cipher_specs]}")

    print("\n== 长度声明超出缓冲区: 报告缺口而非截断 ==")
    truncated = bytes([23]) + VERSION.to_bytes(2, "big") + (100).to_bytes(2, "big") + b"abc"
    try:
        parse_record(truncated)
    except NeedMoreDataError as exc:
        print(f"  NeedMoreDataError: {exc} (missing={exc.missing})")


if __name__ == "__main__":
    main()
