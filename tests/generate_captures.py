"""生成自测/样例所需的 recordlayer 捕获文件（确定性数据）。

生成三个文件到 captures/ 目录：

* handshake_reassembly.bin —— 纯握手流，消息刻意横跨多条记录，
  供“流式重组 vs 一次性读取”对拍使用；
* mixed_dispatch.bin       —— 五种记录类型混合，供类型分发样例使用；
* truncated_*.bin          —— 长度声明超过缓冲区的截断样本，
  附 manifest.json 说明还缺多少字节。

用法::

    python3 tests/generate_captures.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from recordlayer import (
    CONTENT_CHANGE_CIPHER_SPEC,
    CONTENT_ALERT,
    CONTENT_HANDSHAKE,
    CONTENT_APPLICATION_DATA,
)

VERSION = (3, 3)  # TLS 1.2 记录层版本号
HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(os.path.dirname(HERE), "captures")


def record(content_type, body, version=VERSION):
    major, minor = version
    return bytes([content_type, major, minor, len(body) >> 8, len(body) & 0xFF]) + body


def handshake(msg_type, body, message_seq, version=VERSION):
    length = len(body)
    header = bytes(
        [
            msg_type,
            (length >> 16) & 0xFF,
            (length >> 8) & 0xFF,
            length & 0xFF,
            (message_seq >> 8) & 0xFF,
            message_seq & 0xFF,
        ]
    )
    return header + bytes(body)


def build_handshake_stream():
    """构造 4 条握手消息，再故意切得粉碎后塞进多条记录。

    切点同时落在：消息体之间、消息头中间、消息体中间。
    """
    messages = [
        handshake(1, bytes(range(1, 11)), 0),                    # 10 字节体
        handshake(2, bytes([0xAA] * 200), 1),                    # 大消息跨多记录
        handshake(11, b"certificate-fragment" * 8, 2),          # 160 字节
        handshake(16, b"", 3),                                   # 零长度消息（边界）
    ]
    blob = b"".join(messages)

    # 手工设计切点，保证各种切法都出现
    fragments = []
    cut_points = [1, 2, 4, 6, 10, 13, 255, 500, len(blob)]
    start = 0
    for end in cut_points:
        fragments.append(blob[start:end])
        start = end
    assert b"".join(fragments) == blob

    return b"".join(record(CONTENT_HANDSHAKE, frag) for frag in fragments), [
        {"msg_type": 1, "message_seq": 0, "body_length": 10},
        {"msg_type": 2, "message_seq": 1, "body_length": 200},
        {"msg_type": 11, "message_seq": 2, "body_length": 160},
        {"msg_type": 16, "message_seq": 3, "body_length": 0},
    ]


def build_mixed_stream():
    """五种类型按顺序出现，且握手消息被切成三段。"""
    full = handshake(4, b"ABCD", 10)  # 共 10 字节：6 头 + 4 体，切成三段
    parts = [
        record(CONTENT_CHANGE_CIPHER_SPEC, b"\x01"),
        record(CONTENT_HANDSHAKE, full[:3]),
        record(CONTENT_HANDSHAKE, full[3:7]),
        record(CONTENT_HANDSHAKE, full[7:]),
        record(CONTENT_ALERT, bytes([2, 40])),          # fatal / handshake_failure
        record(CONTENT_ALERT, bytes([1, 90])),          # warning / 用户取消
        record(CONTENT_APPLICATION_DATA, b"hello"),
        record(CONTENT_APPLICATION_DATA, b"secret-bytes"),
        record(0x55, b"\xde\xad\xbe\xef"),              # 未知类型
        record(CONTENT_HANDSHAKE, handshake(0, b"tail", 11)),  # 未知类型之后仍可握手
        record(CONTENT_APPLICATION_DATA, b"bye"),
    ]
    expected_events = [
        {"kind": "ChangeCipherSpec"},
        {"kind": "HandshakeMessage", "msg_type": 4, "message_seq": 10,
         "body_length": 4},
        {"kind": "Alert", "level": 2, "description": 40},
        {"kind": "Alert", "level": 1, "description": 90},
        {"kind": "ApplicationData", "length": 5},
        {"kind": "ApplicationData", "length": 12},
        {"kind": "UnknownRecord", "content_type": 0x55, "length": 4},
        {"kind": "HandshakeMessage", "msg_type": 0, "message_seq": 11,
         "body_length": 4},
        {"kind": "ApplicationData", "length": 3},
    ]
    return b"".join(parts), expected_events


def build_truncated_samples():
    """两个截断样本：半截帧头、帧体声明过大。"""
    full_alert = record(CONTENT_ALERT, bytes([2, 40]))
    handshake_blob = record(CONTENT_HANDSHAKE, handshake(2, b"x" * 100, 7))

    header_truncated = full_alert[:3]  # 5 字节头只到 3 字节
    body_truncated = handshake_blob[:-40]  # 声明 106 字节帧，只给 66

    return [
        {
            "filename": "truncated_header.bin",
            "data": header_truncated,
            "missing": 2,
            "reason": "5 字节记录头只收到 3 字节，还缺 2 字节",
        },
        {
            "filename": "truncated_body.bin",
            "data": body_truncated,
            "missing": 40,
            "reason": "长度字段声明 106 字节分片，实际只有 66 字节，还缺 40 字节",
        },
    ]


def main():
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    manifest = {}

    hs_blob, hs_expected = build_handshake_stream()
    hs_path = os.path.join(CAPTURE_DIR, "handshake_reassembly.bin")
    with open(hs_path, "wb") as f:
        f.write(hs_blob)
    manifest["handshake_reassembly.bin"] = {
        "purpose": "握手消息跨记录重组对拍（流式切块 vs 一次性读取）",
        "bytes": len(hs_blob),
        "expected_events": hs_expected,
    }

    mixed_blob, mixed_expected = build_mixed_stream()
    mixed_path = os.path.join(CAPTURE_DIR, "mixed_dispatch.bin")
    with open(mixed_path, "wb") as f:
        f.write(mixed_blob)
    manifest["mixed_dispatch.bin"] = {
        "purpose": "记录类型分发样例（CCS/握手/告警/应用数据/未知类型混合）",
        "bytes": len(mixed_blob),
        "expected_events": mixed_expected,
    }

    for sample in build_truncated_samples():
        path = os.path.join(CAPTURE_DIR, sample["filename"])
        with open(path, "wb") as f:
            f.write(sample["data"])
        manifest[sample["filename"]] = {
            "purpose": "长度声明超过缓冲区的截断样本",
            "bytes": len(sample["data"]),
            "missing": sample["missing"],
            "reason": sample["reason"],
        }

    with open(os.path.join(CAPTURE_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    for name, info in manifest.items():
        print("%-30s %4d 字节  %s" % (name, info["bytes"], info["purpose"]))


if __name__ == "__main__":
    main()
