"""自测：参考表逐项一致、往返对拍、缓存稳定性、边界用例。

直接运行：python3 tests/test_layout.py
或：       python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binlayout import (  # noqa: E402
    array,
    bitgroup,
    clear_cache,
    compute_layout,
    decode,
    encode,
    layout_table,
    struct,
    uint,
)
import examples  # noqa: E402


# 手算参考表（path, offset, size, align, pad_before, note）。
EXPECTED_INNER = [
    ("Inner", 0, 8, 4, 0, "末尾补齐 3"),
    ("Inner.a", 0, 4, 4, 0, ""),
    ("Inner.b", 4, 1, 1, 0, ""),
]

EXPECTED_PACKET = [
    ("Packet", 0, 20, 4, 0, "末尾补齐 3"),
    ("Packet.magic", 0, 1, 1, 0, ""),
    ("Packet.flags", 1, 1, 1, 0, ""),
    ("Packet.flags.version", 1, 0, 0, 0, "bits 0..2"),
    ("Packet.flags.kind", 1, 0, 0, 0, "bits 3..7"),
    ("Packet.count", 2, 2, 2, 0, ""),
    ("Packet.tags", 4, 3, 1, 0, ""),
    ("Packet.tags[]", 4, 1, 1, 0, "stride 1 x 3"),
    ("Packet.inner", 8, 8, 4, 1, "末尾补齐 3"),
    ("Packet.inner.a", 8, 4, 4, 0, ""),
    ("Packet.inner.b", 12, 1, 1, 0, ""),
    ("Packet.tail", 16, 1, 1, 0, ""),
]


def _rows(node):
    return [(r["path"], r["offset"], r["size"], r["align"],
             r["pad_before"], r["note"]) for r in layout_table(node)]


class ReferenceLayoutTest(unittest.TestCase):
    def test_inner_matches_hand_table(self):
        self.assertEqual(_rows(compute_layout(examples.inner)), EXPECTED_INNER)

    def test_packet_matches_hand_table(self):
        self.assertEqual(_rows(compute_layout(examples.packet)),
                         EXPECTED_PACKET)

    def test_layout_offsets_padding_and_size(self):
        layout = compute_layout(examples.packet)
        self.assertEqual(layout.size, 20)
        self.assertEqual(layout.align, 4)
        self.assertEqual(layout.tail_padding, 3)
        by_name = {m.name: m for m in layout.members}
        self.assertEqual(by_name["inner"].offset, 8)
        self.assertEqual(by_name["inner"].padding_before, 1)
        self.assertEqual(by_name["count"].offset, 2)
        inner_layout = by_name["inner"].node
        self.assertEqual(inner_layout.size, 8)
        self.assertEqual(inner_layout.tail_padding, 3)


class CacheStabilityTest(unittest.TestCase):
    def test_repeated_compute_is_identical_object(self):
        a = compute_layout(examples.packet)
        b = compute_layout(examples.packet)
        self.assertIs(a, b)
        self.assertEqual(_rows(a), _rows(b))

    def test_recompute_after_cache_clear_is_equal(self):
        a = compute_layout(examples.packet)
        clear_cache()
        b = compute_layout(examples.packet)
        self.assertEqual(a, b)
        self.assertEqual(_rows(a), _rows(b))
        self.assertIsNot(a, b)

    def test_key_order_does_not_change_layout(self):
        desc1 = uint(16, signed=False, endian="little")
        desc2 = {"endian": "little", "signed": False, "bits": 16,
                 "kind": "uint"}
        self.assertEqual(compute_layout(desc1), compute_layout(desc2))


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.layout = compute_layout(examples.packet)

    def test_fresh_encode_uses_zero_padding(self):
        data = encode(self.layout, examples.packet_values)
        self.assertEqual(len(data), 20)
        self.assertEqual(data[7], 0)
        self.assertEqual(data[13:16], b"\x00\x00\x00")
        self.assertEqual(data[17:20], b"\x00\x00\x00")

    def test_decode_ignores_padding_values(self):
        zero = encode(self.layout, examples.packet_values)
        nonzero = bytes.fromhex(examples.packet_hex_nonzero_padding)
        self.assertEqual(decode(self.layout, zero).values,
                         decode(self.layout, nonzero).values)

    def test_nonzero_padding_is_preserved_on_rewrite(self):
        original = bytes.fromhex(examples.packet_hex_nonzero_padding)
        decoded = decode(self.layout, original)
        self.assertEqual(decoded.values, examples.packet_values)
        self.assertEqual(encode(self.layout, decoded), original)

    def test_modifying_fields_keeps_old_padding(self):
        original = bytearray.fromhex(examples.packet_hex_nonzero_padding)
        decoded = decode(self.layout, original)
        decoded.values["magic"] = 0x01
        decoded.values["inner"]["a"] = 0x11223344
        rewritten = encode(self.layout, decoded)
        original[0] = 0x01
        original[8:12] = bytes.fromhex("44332211")
        self.assertEqual(rewritten, bytes(original))

    def test_data_roundtrip_cases_file(self):
        data_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "roundtrip_cases.json")
        with open(data_path, encoding="utf-8") as handle:
            cases = json.load(handle)
        for case in cases:
            original = bytes.fromhex(case["hex"])
            decoded = decode(self.layout, original)
            self.assertEqual(decoded.values, case["values"], case["name"])
            self.assertEqual(encode(self.layout, decoded), original,
                             case["name"])


class BitFieldTest(unittest.TestCase):
    def test_msb_bit_offsets(self):
        layout = compute_layout(struct("H", ("flags", examples.msb_flags)))
        group = layout.members[0].node
        self.assertEqual([(f.name, f.bit_offset, f.width)
                          for f in group.fields],
                         [("a", 11, 5), ("b", 7, 4), ("c", 0, 7)])

    def test_msb_encode(self):
        layout = struct("H", ("flags", examples.msb_flags))
        encoded = encode(layout, {"flags": {"a": 3, "b": 2, "c": 5}})
        # a=3 -> bits 11..15 (0x1800)，b=2 -> bits 7..10 (0x0100)，c=5 -> bits 0..6
        self.assertEqual(encoded.hex(), "0519")
        self.assertEqual(decode(layout, encoded).values,
                         {"flags": {"a": 3, "b": 2, "c": 5}})

    def test_reserved_bits_preserved_on_rewrite(self):
        layout = struct("R", ("flags", bitgroup(8, (("x", 3), ("y", 2)))))
        self.assertEqual(encode(layout, {"flags": {"x": 1, "y": 2}}).hex(),
                         "11")
        with_reserved = bytes.fromhex("f1")  # 未分配的 bit5..7 写成 1
        decoded = decode(layout, with_reserved)
        self.assertEqual(decoded.values, {"flags": {"x": 1, "y": 2}})
        self.assertEqual(encode(layout, decoded), with_reserved)

    def test_bit_value_out_of_range(self):
        layout = struct("G", ("flags",
                              bitgroup(8, (("x", 4),))))
        with self.assertRaises(ValueError):
            encode(layout, {"flags": {"x": 16}})

    def test_bitgroup_overflow_rejected(self):
        with self.assertRaises(ValueError):
            bitgroup(8, (("x", 5), ("y", 4)))


class EdgeCaseTest(unittest.TestCase):
    def test_empty_struct_size_zero(self):
        layout = compute_layout(struct("Empty"))
        self.assertEqual(layout.size, 0)
        self.assertEqual(layout.align, 1)
        self.assertEqual(encode(layout, {}), b"")

    def test_array_of_structs_stride(self):
        desc = struct("Holder", ("items", array(examples.inner, 2)))
        layout = compute_layout(desc)
        self.assertEqual(layout.size, 16)
        values = {"items": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]}
        data = encode(layout, values)
        self.assertEqual(decode(layout, data).values, values)
        self.assertEqual(len(data), 16)

    def test_signed_big_endian(self):
        desc = struct("S", ("v", uint(16, signed=True, endian="big")))
        layout = compute_layout(desc)
        self.assertEqual(encode(layout, {"v": -2}).hex(), "fffe")
        self.assertEqual(decode(layout, bytes.fromhex("fffe")).values,
                         {"v": -2})

    def test_wrong_data_length_rejected(self):
        layout = compute_layout(examples.packet)
        with self.assertRaises(ValueError):
            decode(layout, b"\x00" * 19)

    def test_bad_descriptors_rejected(self):
        with self.assertRaises(ValueError):
            uint(7)
        with self.assertRaises(ValueError):
            array(uint(8), 0)
        with self.assertRaises(ValueError):
            bitgroup(8, ())
        with self.assertRaises(ValueError):
            struct("D", ("x", uint(8)), ("x", uint(8)))

    def test_unsigned_overflow_and_missing_field(self):
        layout = compute_layout(examples.inner)
        with self.assertRaises(ValueError):
            encode(layout, {"a": 2 ** 32, "b": 0})
        with self.assertRaises(ValueError):
            encode(layout, {"a": 0})

    def test_readwrite_share_one_descriptor(self):
        desc = examples.packet
        layout = compute_layout(desc)
        data = encode(desc, examples.packet_values)
        decoded = decode(desc, data)
        self.assertEqual(decoded.layout, layout)
        self.assertEqual(encode(desc, decoded), data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
