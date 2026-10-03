"""framemux 自测（仅标准库 unittest）。

运行:
    python3 test_framemux.py        # 详细输出 + 断言/状态变化数据
    python3 -m unittest -v test_framemux

覆盖:
    1. 单信道收发与按序拼接（顺序断言）
    2. 多信道交错到达（顺序断言，各自独立保序）
    3. 信道号耗尽
    4. 帧被截断（流结束残留半帧，报错带偏移）
    5. LENGTH 与实际负载不一致（报错带偏移）
    6. 信道分配/回收/复用的状态变化数据
    7. 逐字节喂入（增量解码）与重复释放、未开信道发送等非法状态
"""

import unittest

from framemux import (
    HEADER_SIZE,
    MAX_CHANNEL,
    ChannelAllocator,
    ChannelExhaustedError,
    ChannelStateError,
    FrameDecoder,
    FrameError,
    Multiplexer,
    TruncatedFrameError,
    TYPE_DATA,
    TYPE_OPEN,
    encode_frame,
)


def assert_in_order(testcase, chunks, expected):
    """顺序断言：分块到达的负载必须严格按到达顺序拼接。"""
    testcase.assertEqual(chunks, expected, "chunk arrival order violated")
    testcase.assertEqual(
        b"".join(chunks), b"".join(expected), "reassembly order violated"
    )


class TestSingleChannel(unittest.TestCase):
    def test_roundtrip_and_order(self):
        mux = Multiplexer()
        ch = mux.open_channel()
        payloads = [b"first:", b"second:", b"third"]
        for p in payloads:
            mux.feed(mux.send(ch, p))

        assert_in_order(self, mux.chunks(ch), payloads)
        self.assertEqual(mux.reassembled(ch), b"first:second:third")
        print(f"[order] channel={ch} chunks={payloads} "
              f"-> {mux.reassembled(ch)!r} OK")

    def test_header_fields(self):
        raw = encode_frame(TYPE_OPEN, 7, b"hi")
        magic, ftype, channel, length = raw[:2], raw[2], (raw[3] << 8) | raw[4], int.from_bytes(raw[5:9], "big")
        self.assertEqual((magic, ftype, channel, length), (b"\xc3\x9e", TYPE_OPEN, 7, 2))
        self.assertEqual(length, len(b"hi"))

    def test_empty_payload(self):
        mux = Multiplexer()
        ch = mux.open_channel()
        mux.feed(mux.send(ch, b""))
        self.assertEqual(mux.chunks(ch), [b""])


class TestInterleavedChannels(unittest.TestCase):
    def test_interleaving_preserves_per_channel_order(self):
        mux = Multiplexer()
        a, b, c = mux.open_channel(), mux.open_channel(), mux.open_channel()
        stream = b""
        plan = [
            (a, b"A1"), (b, b"B1"), (a, b"A2"),
            (c, b"C1"), (b, b"B2"), (a, b"A3"),
            (c, b"C2"),
        ]
        for channel, p in plan:
            stream += mux.send(channel, p)

        # 交错的帧一次性到达
        mux.feed(stream)

        assert_in_order(self, mux.chunks(a), [b"A1", b"A2", b"A3"])
        assert_in_order(self, mux.chunks(b), [b"B1", b"B2"])
        assert_in_order(self, mux.chunks(c), [b"C1", b"C2"])
        self.assertEqual(mux.reassembled(a), b"A1A2A3")
        self.assertEqual(mux.reassembled(b), b"B1B2")
        self.assertEqual(mux.reassembled(c), b"C1C2")
        print(f"[order] interleaved {len(plan)} frames on 3 channels OK")

    def test_byte_by_byte_feed(self):
        mux = Multiplexer()
        ch = mux.open_channel()
        raw = mux.send(ch, b"xyz") + mux.send(ch, b"abc")
        for byte in raw:
            mux.feed(bytes([byte]))
        mux.close()
        assert_in_order(self, mux.chunks(ch), [b"xyz", b"abc"])


class TestChannelAllocation(unittest.TestCase):
    def test_allocate_release_reuse(self):
        alloc = ChannelAllocator(max_channel=5)
        transitions = [("init", alloc.snapshot())]
        ids = [alloc.allocate() for _ in range(3)]
        transitions.append(("allocate x3", alloc.snapshot()))
        self.assertEqual(ids, [1, 2, 3])

        alloc.release(2)
        transitions.append(("release 2", alloc.snapshot()))
        self.assertFalse(alloc.is_allocated(2))

        reused = alloc.allocate()
        transitions.append(("allocate (reuse)", alloc.snapshot()))
        self.assertEqual(reused, 2, "released channel id must be reused")

        alloc.release(1)
        alloc.release(2)
        alloc.release(3)
        transitions.append(("release all", alloc.snapshot()))
        self.assertEqual(alloc.snapshot(), [])

        for action, snap in transitions:
            print(f"[alloc] {action:18s} allocated={snap}")

    def test_exhaustion(self):
        alloc = ChannelAllocator(max_channel=3)
        got = {alloc.allocate() for _ in range(3)}
        self.assertEqual(got, {1, 2, 3})
        with self.assertRaises(ChannelExhaustedError):
            alloc.allocate()
        alloc.release(2)
        self.assertEqual(alloc.allocate(), 2)  # 回收后立即可复用
        print("[exhaust] 3/3 channels used -> ChannelExhaustedError OK")

    def test_double_release(self):
        alloc = ChannelAllocator()
        ch = alloc.allocate()
        alloc.release(ch)
        with self.assertRaises(ChannelStateError):
            alloc.release(ch)

    def test_full_range_exhaustion_small(self):
        alloc = ChannelAllocator(max_channel=1)
        alloc.allocate()
        with self.assertRaises(ChannelExhaustedError):
            alloc.allocate()


class TestFramingErrors(unittest.TestCase):
    def test_truncated_frame_reports_offset(self):
        mux = Multiplexer()
        ch = mux.open_channel()
        raw = mux.send(ch, b"abc") + mux.send(ch, b"def")

        # 第二帧只到 3 个字节（头部都不完整）
        mux.feed(raw[: HEADER_SIZE + 3 + 3])
        with self.assertRaises(TruncatedFrameError) as ctx:
            mux.close()
        self.assertEqual(ctx.exception.offset, HEADER_SIZE + 3)
        print(f"[truncated] error offset={ctx.exception.offset} "
              f"expected>={ctx.exception.expected} actual={ctx.exception.actual} OK")

        # 头部完整但负载被截断
        dec = FrameDecoder()
        dec.feed(encode_frame(TYPE_DATA, 1, b"abcdef")[: HEADER_SIZE + 2])
        with self.assertRaises(TruncatedFrameError) as ctx:
            dec.close()
        self.assertEqual(ctx.exception.offset, 0)
        self.assertEqual(ctx.exception.expected, HEADER_SIZE + 6)
        self.assertEqual(ctx.exception.actual, HEADER_SIZE + 2)

    def test_length_mismatch_reports_offset(self):
        # 构造 LENGTH=5 但实际只放 3 字节负载，再拼一个合法帧头
        bad_head = encode_frame(TYPE_DATA, 1, b"abc")[:HEADER_SIZE]
        bad = bad_head[:5] + (5).to_bytes(4, "big") + b"abc"
        good = encode_frame(TYPE_DATA, 2, b"Z")
        dec = FrameDecoder()
        dec.feed(bad)  # 流尚未结束，等待“第 5 个负载字节”
        with self.assertRaises(FrameError) as ctx:
            dec.feed(good + good)  # 数据到齐后帧边界对不上魔数
        # 错误位置 = 上一帧声明的边界偏移（9 头 + 5 声明负载）
        self.assertEqual(ctx.exception.offset, HEADER_SIZE + 5)
        print(f"[length-mismatch] LENGTH=5 actual=3 -> error at "
              f"offset={ctx.exception.offset} OK")

    def test_garbage_stream_reports_offset(self):
        dec = FrameDecoder()
        with self.assertRaises(FrameError) as ctx:
            dec.feed(b"\x00" * HEADER_SIZE + b"junk")
        self.assertEqual(ctx.exception.offset, 0)

    def test_send_on_closed_channel(self):
        mux = Multiplexer()
        ch = mux.open_channel()
        mux.close_channel(ch)
        with self.assertRaises(ChannelStateError):
            mux.send(ch, b"x")


class TestFullRangeConstants(unittest.TestCase):
    def test_channel_bounds(self):
        encode_frame(TYPE_DATA, MAX_CHANNEL, b"")
        with self.assertRaises(ValueError):
            encode_frame(TYPE_DATA, MAX_CHANNEL + 1, b"")


if __name__ == "__main__":
    print("=" * 64)
    print("framemux self-test: order assertions + allocation transitions")
    print("=" * 64)
    unittest.main(verbosity=2)
