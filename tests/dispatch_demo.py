"""记录类型分发样例：解析 captures/mixed_dispatch.bin，按类型归类打印。

用法::

    python3 tests/dispatch_demo.py                      # 内置样例
    python3 tests/dispatch_demo.py path/to/capture.bin  # 任意捕获文件
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from recordlayer import (  # noqa: E402
    RecordAssembler,
    HandshakeMessage,
    Alert,
    ChangeCipherSpec,
    ApplicationData,
    UnknownRecord,
    RecordLayerError,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CAPTURE = os.path.join(os.path.dirname(HERE), "captures", "mixed_dispatch.bin")


def describe(event):
    if isinstance(event, HandshakeMessage):
        return "握手消息 type=%d seq=%d 体长=%d 字节" % (
            event.msg_type,
            event.message_seq,
            len(event.body),
        )
    if isinstance(event, Alert):
        return "告警 level=%d(%s) description=%d raw=%s" % (
            event.level,
            event.level_name,
            event.description,
            event.raw.hex(),
        )
    if isinstance(event, ChangeCipherSpec):
        return "ChangeCipherSpec raw=%s" % event.raw.hex()
    if isinstance(event, ApplicationData):
        return "应用数据 %d 字节: %r" % (len(event.data), event.data)
    if isinstance(event, UnknownRecord):
        return "未知记录类型 0x%02x，%d 字节原样保留: %s" % (
            event.content_type,
            len(event.body),
            event.body.hex(),
        )
    return repr(event)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CAPTURE
    with open(path, "rb") as f:
        data = f.read()

    # 模拟真实传输：数据以不规则的块到达
    chunk_sizes = [3, 7, 1, 20, 5, 64, 2]
    assembler = RecordAssembler()
    buckets = {"握手": [], "告警": [], "应用数据": [], "其他": []}
    index = 0
    pos = 0
    while pos < len(data):
        size = chunk_sizes[index % len(chunk_sizes)]
        index += 1
        chunk = data[pos : pos + size]
        pos += len(chunk)
        for event in assembler.feed(chunk):
            if isinstance(event, HandshakeMessage):
                buckets["握手"].append(event)
            elif isinstance(event, Alert):
                buckets["告警"].append(event)
            elif isinstance(event, ApplicationData):
                buckets["应用数据"].append(event)
            else:
                buckets["其他"].append(event)
    assembler.finish()

    print("捕获文件: %s（%d 字节，分 %d 块喂入）" % (path, len(data), index))
    for name, events in buckets.items():
        print("\n[%s] 共 %d 条" % (name, len(events)))
        for event in events:
            print("  - %s" % describe(event))

    total = sum(len(v) for v in buckets.values())
    print("\n合计 %d 条事件；告警与应用数据严格分离，未知类型原样保留。" % total)


if __name__ == "__main__":
    try:
        main()
    except RecordLayerError as exc:
        print("解析失败: %s" % exc, file=sys.stderr)
        sys.exit(1)
