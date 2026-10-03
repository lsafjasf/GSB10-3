"""重组器自测：python3 -m unittest -v test_reassembly"""

import unittest

from reassembly import (
    Delivery,
    EvictionRecord,
    Fragment,
    FragmentTooLargeError,
    ProtocolError,
    Reassembler,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def add(reasm, gid, offset, data, final=False):
    return reasm.add(Fragment(gid, offset, data, final))


class NormalReassemblyTests(unittest.TestCase):
    def test_in_order(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=clk)
        self.assertIsNone(add(reasm, "m", 0, b"hello "))
        self.assertIsNone(add(reasm, "m", 6, b"world!"))
        delivery = add(reasm, "m", 12, b"", final=True)
        self.assertIsInstance(delivery, Delivery)
        self.assertEqual(delivery.payload, b"hello world!")
        self.assertEqual(delivery.overlap_bytes, 0)
        self.assertEqual(reasm.used_bytes, 0)  # 交付即释放
        self.assertEqual(reasm.buffered_groups, 0)

    def test_single_fragment_message(self):
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=FakeClock())
        delivery = add(reasm, "s", 0, b"only one", final=True)
        self.assertEqual(delivery.payload, b"only one")

    def test_empty_message(self):
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=FakeClock())
        delivery = add(reasm, "e", 0, b"", final=True)
        self.assertEqual(delivery.payload, b"")
        self.assertEqual(reasm.used_bytes, 0)

    def test_out_of_order(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=100, max_total_bytes=64, time_func=clk)
        # 尾分片最先到
        self.assertIsNone(add(reasm, "x", 6, b"world!", final=True))
        self.assertEqual(reasm.used_bytes, 6)
        # 头部后到，与尾分片拼合即交付
        delivery = add(reasm, "x", 0, b"hello ")
        self.assertEqual(delivery.payload, b"hello world!")
        self.assertEqual(reasm.buffered_groups, 0)

    def test_out_of_order_gap_closed_later(self):
        reasm = Reassembler(timeout=100, max_total_bytes=64, time_func=FakeClock())
        self.assertIsNone(add(reasm, "y", 0, b"aaa"))
        self.assertIsNone(add(reasm, "y", 6, b"ggg", final=True))  # [6,9)，空洞 [3,6)
        self.assertEqual(reasm.buffered_groups, 1)
        delivery = add(reasm, "y", 3, b"bbb")
        self.assertEqual(delivery.payload, b"aaabbbggg")
        self.assertEqual(reasm.used_bytes, 0)


class OverlapTests(unittest.TestCase):
    def test_first_arrival_wins(self):
        reasm = Reassembler(timeout=100, max_total_bytes=64, time_func=FakeClock())
        add(reasm, "o", 0, b"AAAA")            # [0,4)
        delivery = add(reasm, "o", 2, b"BBBB", final=True)  # 覆盖 [2,6)，重叠 [2,4)
        self.assertEqual(delivery.payload, b"AAAABB")  # [2,4) 保留先到的 AA
        self.assertEqual(delivery.overlap_bytes, 2)

    def test_overlap_sample_doc_scenario(self):
        # 与 demo / 文档一致的重叠样例
        reasm = Reassembler(timeout=100, max_total_bytes=64, time_func=FakeClock())
        add(reasm, "p", 0, b"hello wo")          # [0,8)
        add(reasm, "p", 4, b"o world!")          # [4,12)，重叠 [4,8) 4 字节
        delivery = add(reasm, "p", 12, b"", final=True)
        self.assertEqual(delivery.payload, b"hello world!")
        self.assertEqual(delivery.overlap_bytes, 4)

    def test_duplicate_fragment_costs_nothing(self):
        reasm = Reassembler(timeout=100, max_total_bytes=8, time_func=FakeClock())
        add(reasm, "d", 0, b"abcd")
        self.assertEqual(reasm.used_bytes, 4)
        self.assertIsNone(add(reasm, "d", 0, b"abcd"))  # 完全重复
        self.assertEqual(reasm.used_bytes, 4)

    def test_bytes_beyond_known_end_are_clipped(self):
        reasm = Reassembler(timeout=100, max_total_bytes=64, time_func=FakeClock())
        add(reasm, "c", 0, b"abcde", final=True)  # 总长 5，直接收齐
        self.assertEqual(reasm.buffered_groups, 0)
        # 收齐之后再来超长分片：组已不存在，按新组处理；这里改测先声明长度再超长
        reasm2 = Reassembler(timeout=100, max_total_bytes=64, time_func=FakeClock())
        add(reasm2, "c2", 0, b"ab")          # [0,2)
        add(reasm2, "c2", 4, b"ef", final=True)  # 总长 6，留空洞 [2,4)
        # 声称覆盖 [3,7) 的分片：[6,7) 超尾被裁，[4,6) 重叠，仅 [3,4) 是新字节
        self.assertIsNone(add(reasm2, "c2", 3, b"XXXX"))
        self.assertEqual(reasm2.used_bytes, 5)
        delivery = add(reasm2, "c2", 2, b"c")  # 补上最后一个空洞字节
        self.assertEqual(delivery.payload, b"abcXef")


class TimeoutTests(unittest.TestCase):
    def test_missing_fragment_times_out(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=clk)
        add(reasm, "m", 0, b"ab")
        add(reasm, "m", 2, b"cd")  # 无尾分片、空洞未知 —— 永久缺失场景
        self.assertEqual(reasm.used_bytes, 4)

        clk.advance(9.999)
        self.assertEqual(reasm.sweep(), [])  # 边界：差一点不过期
        self.assertEqual(reasm.buffered_groups, 1)

        clk.advance(0.001)
        records = reasm.sweep()  # t == created+10，判据为 >=
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.reason, "timeout")
        self.assertEqual(rec.identification, "m")
        self.assertEqual(rec.age, 10.0)
        self.assertEqual(rec.bytes_freed, 4)
        self.assertIsNone(rec.total_length)
        self.assertEqual(reasm.used_bytes, 0)       # 资源已释放
        self.assertEqual(reasm.buffered_groups, 0)

    def test_later_fragments_do_not_extend_lifetime(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=clk)
        add(reasm, "m", 0, b"ab")
        clk.advance(9.0)
        add(reasm, "m", 2, b"cd")  # 新分片不应把寿命顺延到 t=19
        clk.advance(0.999)
        self.assertEqual(reasm.buffered_groups, 1)
        clk.advance(0.001)  # 距建组正好 10
        self.assertEqual(len(reasm.sweep()), 1)

    def test_timeout_with_known_hole(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=5, max_total_bytes=100, time_func=clk)
        add(reasm, "h", 0, b"aaa")
        add(reasm, "h", 6, b"ggg", final=True)  # 总长 9，空洞 [3,6) 永远不来
        clk.advance(5)
        records = reasm.sweep()
        self.assertEqual(records[0].reason, "timeout")
        self.assertEqual(records[0].total_length, 9)
        self.assertEqual(records[0].covered_bytes, 6)
        self.assertEqual(records[0].bytes_freed, 6)
        self.assertEqual(reasm.used_bytes, 0)

    def test_sweep_is_implicit_on_add(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=clk)
        add(reasm, "old", 0, b"x")
        clk.advance(20)
        add(reasm, "new", 0, b"y")  # 任何 add 都会先清扫
        self.assertEqual(reasm.buffered_groups, 1)
        self.assertEqual(reasm.eviction_log[0].reason, "timeout")


class CapacityEvictionTests(unittest.TestCase):
    def test_lru_eviction_order_and_records(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=1000, max_total_bytes=10, time_func=clk)
        add(reasm, "A", 0, b"aaaa")      # t=0, used=4
        clk.advance(1)
        add(reasm, "B", 0, b"bbbb")      # t=1, used=8
        clk.advance(1)
        add(reasm, "C", 0, b"cccccc")    # t=2, 需要 6；A 最久未更新被清退
        self.assertEqual(reasm.used_bytes, 10)
        self.assertEqual(reasm.buffered_groups, 2)
        rec_a = reasm.eviction_log[0]
        self.assertEqual((rec_a.identification, rec_a.reason, rec_a.bytes_freed),
                         ("A", "capacity", 4))

        clk.advance(1)
        add(reasm, "B", 0, b"bbbb")      # 纯重复分片：只刷新 B 的 LRU 时间戳
        clk.advance(1)
        add(reasm, "D", 0, b"dd")        # t=4, 需要 2；C(last_seen=2) 比 B(t=3) 更久
        identifications = [r.identification for r in reasm.eviction_log]
        self.assertEqual(identifications, ["A", "C"])
        self.assertEqual(reasm.used_bytes, 6)  # B=4, D=2

    def test_eviction_callback(self):
        events = []
        reasm = Reassembler(timeout=10, max_total_bytes=4,
                            time_func=FakeClock(),
                            on_evict=events.append)
        add(reasm, "a", 0, b"aaaa")
        with self.assertRaises(FragmentTooLargeError):
            add(reasm, "b", 0, b"bbbbb")  # 单分片 > 总容量，直接拒绝
        self.assertEqual(events, [])  # 拒绝前未动任何在途组

    def test_oversized_single_fragment_never_enters_buffer(self):
        reasm = Reassembler(timeout=10, max_total_bytes=4, time_func=FakeClock())
        with self.assertRaises(FragmentTooLargeError):
            add(reasm, "big", 0, b"x" * 5)
        self.assertEqual(reasm.used_bytes, 0)
        self.assertEqual(reasm.buffered_groups, 0)

    def test_alone_group_growing_past_capacity_is_dropped(self):
        reasm = Reassembler(timeout=10, max_total_bytes=4, time_func=FakeClock())
        add(reasm, "solo", 0, b"aaaa")
        with self.assertRaises(FragmentTooLargeError):
            add(reasm, "solo", 4, b"bb")  # 别无他组可退，放弃本组
        self.assertEqual(reasm.used_bytes, 0)
        self.assertEqual(reasm.buffered_groups, 0)
        rec = reasm.eviction_log[0]
        self.assertEqual(rec.reason, "capacity")
        self.assertEqual(rec.identification, "solo")
        self.assertEqual(rec.bytes_freed, 4)

    def test_completed_group_frees_quota(self):
        reasm = Reassembler(timeout=10, max_total_bytes=6, time_func=FakeClock())
        add(reasm, "a", 0, b"aaa")
        delivery = add(reasm, "a", 3, b"bbb", final=True)
        self.assertEqual(delivery.payload, b"aaabbb")
        add(reasm, "b", 0, b"cccccc")  # 前一组交付后释放，新组恰好放得下
        self.assertEqual(reasm.used_bytes, 6)


class ProtocolBoundaryTests(unittest.TestCase):
    def test_negative_offset_rejected(self):
        reasm = Reassembler(timeout=10, max_total_bytes=10, time_func=FakeClock())
        with self.assertRaises(ProtocolError):
            reasm.add(Fragment("z", -1, b"x"))

    def test_conflicting_final_length_rejected(self):
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=FakeClock())
        add(reasm, "z", 0, b"abcdefgh", final=True)  # 直接收齐，组消失
        # 用未收齐的组制造冲突：尾端声明 8
        reasm2 = Reassembler(timeout=10, max_total_bytes=100, time_func=FakeClock())
        add(reasm2, "z", 0, b"ab")
        add(reasm2, "z", 4, b"xxxx", final=True)  # end=8
        with self.assertRaises(ProtocolError):
            add(reasm2, "z", 5, b"yyyyy", final=True)  # end=10 冲突
        self.assertEqual(reasm2.used_bytes, 6)  # [0,2)+[4,8)，冲突分片未写入

    def test_non_bytes_data_rejected(self):
        reasm = Reassembler(timeout=10, max_total_bytes=100, time_func=FakeClock())
        with self.assertRaises(ProtocolError):
            reasm.add(Fragment("z", 0, "not-bytes"))

    def test_record_serializable(self):
        clk = FakeClock()
        reasm = Reassembler(timeout=1, max_total_bytes=10, time_func=clk)
        add(reasm, "r", 0, b"ab")
        clk.advance(1)
        reasm.sweep()
        d = reasm.eviction_log[0].to_dict()
        self.assertEqual(d["reason"], "timeout")
        self.assertIsInstance(d["identification"], str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
