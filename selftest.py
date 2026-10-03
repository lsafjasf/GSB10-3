"""selftest —— frame_mux 的自测与演示。

运行方式：
    python3 selftest.py            # 先打印演示数据，再跑全部测试
    python3 -m unittest selftest -v  # 只跑测试

覆盖：单信道、多信道交错、信道号耗尽、帧被截断，以及长度不一致、
魔数错位、负载超限、随机分段回环等边界情形。
"""

import random
import struct
import unittest

from frame_mux import (
    BadMagicError,
    ChannelExhaustedError,
    ChannelPool,
    Demultiplexer,
    Frame,
    FrameDecoder,
    HEADER_FORMAT,
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    PayloadTooLargeError,
    TruncatedFrameError,
    TYPE_CLOSE,
    TYPE_DATA,
    TYPE_OPEN,
    TYPE_PING,
    decode_all,
    encode_frame,
    multiplex,
)


# ---------------------------------------------------------------- 演示数据输出

def demo_channel_pool():
    print("=" * 64)
    print("[演示 1] 信道号分配 / 回收 / 复用的状态变化（capacity=4）")
    print("=" * 64)
    pool = ChannelPool(capacity=4)
    got = [pool.alloc() for _ in range(3)]          # 1, 2, 3
    pool.release(got[1])                            # 释放 2
    got.append(pool.alloc())                        # 复用 2
    got.append(pool.alloc())                        # 4
    try:
        pool.alloc()                                # 耗尽
    except ChannelExhaustedError as exc:
        print("  ! alloc 失败（信道耗尽）: %s" % exc)
    pool.release(got[0])                            # 释放 1
    got.append(pool.alloc())                        # 复用 1

    print("  %-4s %-8s %-9s %-22s %s" % ("seq", "op", "channel", "allocated(操作后)", "free(操作后)"))
    for ev in pool.events:
        print("  %-4d %-8s %-9d %-22s %d"
              % (ev.seq, ev.op, ev.channel, str(list(ev.allocated)), ev.free_count))
    assert [e.channel for e in pool.events] == [1, 2, 3, 2, 2, 4, 1, 1]
    assert pool.allocated == (1, 2, 3, 4)
    print("  结论：回收的 2、1 被后续 alloc 原样复用；耗尽时 alloc 抛错。")
    print()


def demo_interleaved():
    print("=" * 64)
    print("[演示 2] 多信道交错复用一条流，按信道保序拼接")
    print("=" * 64)
    frames = [
        Frame(TYPE_OPEN, 1, b""),
        Frame(TYPE_DATA, 1, b"hello "),
        Frame(TYPE_DATA, 2, b"[2:a]"),
        Frame(TYPE_DATA, 1, b"world"),
        Frame(TYPE_DATA, 2, b"[2:b]"),
        Frame(TYPE_PING, 0, b""),
        Frame(TYPE_DATA, 1, b"!"),
        Frame(TYPE_CLOSE, 2, b""),
    ]
    stream = multiplex(frames)
    print("  复用后流长 %d 字节（%d 帧交错）" % (len(stream), len(frames)))
    print("  到达顺序（帧序号: 信道/类型/负载）:")
    demux = Demultiplexer()
    demux.feed(decode_all(stream))
    for rec in demux.arrival_log:
        print("    #%d  ch=%d  %-5s %r" % (rec.index, rec.channel,
              {TYPE_OPEN: "OPEN", TYPE_DATA: "DATA", TYPE_PING: "PING",
               TYPE_CLOSE: "CLOSE"}[rec.ftype], rec.payload))
    # 注意：OPEN/CLOSE 等控制帧也占用该信道的到达序列（空负载分片）
    demux.assert_channel_order(1, [b"", b"hello ", b"world", b"!"])
    demux.assert_channel_order(2, [b"[2:a]", b"[2:b]", b""])
    demux.assert_global_order([1, 1, 2, 1, 2, 0, 1, 2])
    print("  ch=1 拼接结果: %r" % demux.payload(1))
    print("  ch=2 拼接结果: %r" % demux.payload(2))
    print("  顺序断言全部通过：交错到达不影响各信道内部的先后顺序。")
    print()


def demo_truncated():
    print("=" * 64)
    print("[演示 3] 帧被截断：length 与实际负载不一致，报错并给出偏移")
    print("=" * 64)
    good = encode_frame(TYPE_DATA, 1, b"OK")
    broken = encode_frame(TYPE_DATA, 2, b"abcdef")[:-2]  # 声明 6 字节，实际 4 字节
    stream = good + broken
    try:
        decode_all(stream)
    except TruncatedFrameError as exc:
        print("  TruncatedFrameError: %s" % exc)
        print("  偏移含义：第 1 帧占 %d 字节，截断帧从 offset=%d 开始，"
              "声明 length=%d，实际只剩 %d 字节负载。"
              % (len(good), exc.offset, exc.declared, exc.available))
    assert True
    print()


def run_demos():
    demo_channel_pool()
    demo_interleaved()
    demo_truncated()


# ---------------------------------------------------------------- 测试用例

class TestSingleChannel(unittest.TestCase):
    """单信道：编解码回环，顺序断言。"""

    def test_roundtrip(self):
        chunks = [b"", b"a", b"hello world", bytes(range(256)), "你好".encode()]
        stream = multiplex(Frame(TYPE_DATA, 7, c) for c in chunks)
        frames = decode_all(stream)
        self.assertEqual([f.channel for f in frames], [7] * len(chunks))
        self.assertEqual([f.payload for f in frames], chunks)

        demux = Demultiplexer()
        demux.feed(frames)
        demux.assert_channel_order(7, chunks)          # 顺序断言
        self.assertEqual(demux.payload(7), b"".join(chunks))

    def test_frame_fields(self):
        frame = decode_all(encode_frame(TYPE_DATA, 3, b"xyz"))[0]
        self.assertEqual((frame.type, frame.channel, frame.payload), (TYPE_DATA, 3, b"xyz"))
        self.assertEqual(frame.type_name, "DATA")


class TestInterleavedChannels(unittest.TestCase):
    """多信道交错：复用一条流，分段 feed，各信道保序。"""

    def test_interleaved(self):
        plan = [  # (信道, 负载) —— 刻意交错
            (1, b"A1"), (2, b"B1"), (1, b"A2"), (3, b"C1"),
            (2, b"B2"), (1, b"A3"), (3, b"C2"), (2, b"B3"),
        ]
        stream = multiplex(Frame(TYPE_DATA, ch, p) for ch, p in plan)

        # 随机切片后逐段 feed，模拟网络分包
        rng = random.Random(20261003)
        decoder, demux = FrameDecoder(), Demultiplexer()
        pos = 0
        while pos < len(stream):
            step = rng.randint(1, 17)
            demux.feed(decoder.feed(stream[pos:pos + step]))
            pos += step
        decoder.finish()

        # 顺序断言：每个信道的分片序列与发送顺序一致
        for ch in (1, 2, 3):
            demux.assert_channel_order(ch, [p for c, p in plan if c == ch])
        # 全局到达顺序也必须与写入顺序一致
        demux.assert_global_order([ch for ch, _ in plan])
        # 拼接结果
        self.assertEqual(demux.payload(1), b"A1A2A3")
        self.assertEqual(demux.payload(2), b"B1B2B3")
        self.assertEqual(demux.payload(3), b"C1C2")


class TestChannelPool(unittest.TestCase):
    """信道号分配 / 回收 / 复用 / 耗尽，以及状态变化数据。"""

    def test_alloc_release_reuse(self):
        pool = ChannelPool(capacity=3)
        a, b, c = pool.alloc(), pool.alloc(), pool.alloc()
        self.assertEqual((a, b, c), (1, 2, 3))
        pool.release(b)                  # 回收 2
        d = pool.alloc()                 # 复用最小空闲号
        self.assertEqual(d, 2)
        self.assertEqual(pool.allocated, (1, 2, 3))
        self.assertEqual(pool.free_count, 0)

        # 状态变化数据：逐条核对快照
        self.assertEqual(
            [(e.op, e.channel, e.allocated, e.free_count) for e in pool.events],
            [
                ("alloc",   1, (1,),     2),
                ("alloc",   2, (1, 2),   1),
                ("alloc",   3, (1, 2, 3), 0),
                ("release", 2, (1, 3),   1),
                ("alloc",   2, (1, 2, 3), 0),
            ],
        )

    def test_exhaustion(self):
        pool = ChannelPool(capacity=2)
        pool.alloc()
        pool.alloc()
        with self.assertRaises(ChannelExhaustedError):
            pool.alloc()
        pool.release(1)                  # 回收后即可再分配
        self.assertEqual(pool.alloc(), 1)

    def test_release_unallocated(self):
        pool = ChannelPool(capacity=2)
        with self.assertRaises(ValueError):
            pool.release(9)
        ch = pool.alloc()
        pool.release(ch)                 # 正常释放
        with self.assertRaises(ValueError):
            pool.release(ch)             # 重复释放


class TestTruncatedFrame(unittest.TestCase):
    """帧被截断 / length 与实际负载不一致：报错并给出偏移。"""

    def test_truncated_payload_offset(self):
        good = encode_frame(TYPE_DATA, 1, b"OK")          # 11 字节
        broken = encode_frame(TYPE_DATA, 2, b"abcdef")[:-2]  # 声明 6 实得 4
        with self.assertRaises(TruncatedFrameError) as ctx:
            decode_all(good + broken)
        exc = ctx.exception
        self.assertEqual(exc.offset, len(good))           # 截断帧的起始偏移
        self.assertEqual(exc.declared, 6)
        self.assertEqual(exc.available, 4)

    def test_truncated_header(self):
        data = encode_frame(TYPE_DATA, 1, b"xy")[:5]      # 头都不完整
        with self.assertRaises(TruncatedFrameError) as ctx:
            decode_all(data)
        self.assertEqual(ctx.exception.offset, 0)

    def test_truncated_second_frame_offset(self):
        f1 = encode_frame(TYPE_DATA, 1, b"12345")         # 14 字节
        f2 = encode_frame(TYPE_DATA, 2, b"zz")
        cut = f1 + f2[:HEADER_SIZE + 1]                   # 第 2 帧只到 1 字节负载
        with self.assertRaises(TruncatedFrameError) as ctx:
            decode_all(cut)
        self.assertEqual(ctx.exception.offset, len(f1))
        self.assertEqual(ctx.exception.available, 1)

    def test_mid_stream_split_is_not_truncation(self):
        stream = encode_frame(TYPE_DATA, 1, b"hello")
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(stream[:4]), [])    # 半个头：先等
        self.assertEqual(decoder.feed(stream[4:]), [Frame(TYPE_DATA, 1, b"hello")])
        decoder.finish()                                  # 不抛错

    def test_length_declared_smaller_desyncs(self):
        # length 声明偏小：帧被错切，后续字节在错位点触发魔数校验失败
        nxt = encode_frame(TYPE_DATA, 2, b"next")
        raw = struct.pack(HEADER_FORMAT, MAGIC, TYPE_DATA, 1, 2) + b"ABCD" + nxt
        with self.assertRaises(BadMagicError) as ctx:
            decode_all(raw)
        self.assertEqual(ctx.exception.offset, HEADER_SIZE + 2)  # 错位点偏移


class TestHeaderValidation(unittest.TestCase):
    """头部字段校验：魔数、length 上限。"""

    def test_bad_magic(self):
        good = encode_frame(TYPE_DATA, 1, b"hi")
        bad = b"\x00\x00" + good[2:]
        with self.assertRaises(BadMagicError) as ctx:
            decode_all(good + bad)
        self.assertEqual(ctx.exception.offset, len(good))

    def test_length_exceeds_max_payload(self):
        raw = struct.pack(HEADER_FORMAT, MAGIC, TYPE_DATA, 1, MAX_PAYLOAD + 1)
        with self.assertRaises(PayloadTooLargeError) as ctx:
            decode_all(raw)
        self.assertEqual(ctx.exception.offset, 5)         # length 字段偏移

    def test_encode_rejects_oversize_payload(self):
        with self.assertRaises(ValueError):
            encode_frame(TYPE_DATA, 1, b"x" * (MAX_PAYLOAD + 1))


class TestFuzzRoundtrip(unittest.TestCase):
    """随机帧序列 + 随机切片 feed 的回环测试。"""

    def test_fuzz(self):
        rng = random.Random(1234)
        for _ in range(50):
            frames = [
                Frame(rng.choice([TYPE_DATA, TYPE_OPEN, TYPE_CLOSE, TYPE_PING]),
                      rng.randint(0, 8),
                      bytes(rng.randrange(256) for _ in range(rng.randint(0, 64))))
                for _ in range(rng.randint(1, 12))
            ]
            stream = multiplex(frames)
            decoder, got = FrameDecoder(), []
            pos = 0
            while pos < len(stream):
                step = rng.randint(1, 23)
                got.extend(decoder.feed(stream[pos:pos + step]))
                pos += step
            decoder.finish()
            self.assertEqual(got, frames)


def run_tests(verbosity=1):
    suite = unittest.TestLoader().loadTestsFromModule(__import__("selftest"))
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    run_demos()
    print("=" * 64)
    print("[自测] 运行全部测试用例")
    print("=" * 64)
    raise SystemExit(0 if run_tests(verbosity=2) else 1)
