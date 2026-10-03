"""示例布局描述：compute_layout / encode / decode 共用同一份描述。"""

from binlayout import array, bitgroup, struct, uint

# 嵌套结构体：u32 + u8，对齐 4，末尾补齐 3，总宽 8。
inner = struct(
    "Inner",
    ("a", uint(32)),
    ("b", uint(8)),
)

# 覆盖：位域、定长数组、嵌套结构、成员前填充、末尾补齐。
packet = struct(
    "Packet",
    ("magic", uint(8)),
    ("flags", bitgroup(8, (("version", 3), ("kind", 5)))),
    ("count", uint(16)),
    ("tags", array(uint(8), 3)),
    ("inner", inner),
    ("tail", uint(8)),
)

# 高位优先位序的位域组。
msb_flags = bitgroup(16, (("a", 5), ("b", 4), ("c", 7)), bitorder="msb")

packet_values = {
    "magic": 0x42,
    "flags": {"version": 5, "kind": 17},
    "count": 0x1234,
    "tags": [1, 2, 3],
    "inner": {"a": 0xDEADBEEF, "b": 0x7F},
    "tail": 0x99,
}

# 填充位置（offset）：7 为 inner 前填充；13..15 为 inner 末尾补齐；17..19 为
# Packet 末尾补齐。下面这组字节把这些位置全部写成非零，用来对拍填充保留。
packet_hex_nonzero_padding = "428d3412010203aaefbeadde7fbbccdd99eeff00"
