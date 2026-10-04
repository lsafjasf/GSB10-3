"""生成握手消息跨记录重组的对拍数据(确定性, 无随机种子依赖)。

每组用例包含:
  messages           —— 原始握手消息(type+3字节长度+body)的 hex
  oneshot_records    —— 全部字节塞进一条握手记录时的记录流 hex
  fragmented_records —— 同一字节流按一组刁钻边界切成多条记录后的 hex 列表

切分边界刻意覆盖: 1 字节碎块、在 4 字节握手头中间切开、
消息边界对齐/不对齐记录边界、尾部悬空等情况。

运行: python3 tools/gen_vectors.py > testdata/reassembly_vectors.json
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tlsrecord import build_handshake, build_record  # noqa: E402

VERSION = 0x0303


def make_body(seed: int, length: int) -> bytes:
    """确定性伪随机 body: 简单线性同余, 避免与长度/header 模式撞车。"""
    state = seed & 0xFFFFFFFF
    out = bytearray()
    for _ in range(length):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        out.append((state >> 16) & 0xFF)
    return bytes(out)


def split_at(data: bytes, cuts):
    """按切点列表切成若干段。"""
    chunks = []
    prev = 0
    for cut in cuts:
        chunks.append(data[prev:cut])
        prev = cut
    chunks.append(data[prev:])
    return [c for c in chunks if c]


CASES = [
    {
        "name": "single_small_message_split_mid_header",
        "messages": [(1, 13, 7)],          # ClientHello, seed=13, body 7 字节
        "cuts": [1, 2, 4],                 # 在握手头(4B)中间切开
    },
    {
        "name": "two_messages_coalesced_then_fragmented",
        "messages": [(1, 1, 50), (2, 2, 100)],
        "cuts": [1, 4, 5, 60, 61, 100, 140],
    },
    {
        "name": "three_messages_byte_by_byte",
        "messages": [(11, 3, 30), (12, 4, 30), (20, 5, 12)],
        # 1 字节碎块 + 3 字节碎块(恰好握手头长)混合
        "cuts": list(range(1, 30)) + list(range(33, 100, 3)),
    },
    {
        "name": "split_exactly_at_message_boundary",
        "messages": [(1, 9, 20), (2, 10, 16)],
        "cuts": [24],                      # 24 = 4+20, 正好消息边界
    },
    {
        "name": "large_message_one_byte_fragments",
        "messages": [(11, 77, 300)],
        "cuts": list(range(1, 304)),       # 全程 1 字节一切
    },
    {
        "name": "empty_body_message",
        "messages": [(0, 8, 0), (20, 9, 5)],
        "cuts": [2, 4, 5],
    },
]


def build_vectors():
    vectors = []
    for case in CASES:
        messages = [
            build_handshake(mtype, make_body(seed, body_len))
            for mtype, seed, body_len in case["messages"]
        ]
        stream = b"".join(messages)
        oneshot = build_record(22, VERSION, stream)
        fragments = split_at(stream, sorted(set(case["cuts"])))
        fragmented = [build_record(22, VERSION, frag) for frag in fragments]
        vectors.append(
            {
                "name": case["name"],
                "description": "oneshot 与 fragmented 承载相同握手字节流, "
                               "重组结果必须完全一致",
                "messages_hex": [m.hex() for m in messages],
                "oneshot_record_hex": oneshot.hex(),
                "fragmented_record_hex": [r.hex() for r in fragmented],
            }
        )
    return vectors


def main():
    json.dump(
        {"version": "0x0303", "content_type": 22, "cases": build_vectors()},
        sys.stdout,
        indent=2,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
