"""自定义二进制结构的布局计算与读写（仅使用标准库，不用 struct 等打包模块）。

布局规则（与 docs/layout_reference.md 中的手算参考表一致）：
- 标量按自身宽度自然对齐（u8->1, u16->2, u32->4, u64->8）。
- 位域组按其存储单元宽度对齐，组内各位段按声明顺序紧凑排列。
- 定长数组按元素对齐，元素紧密排列（元素宽度已是其对齐的整数倍，stride == 元素宽度）。
- 结构体按声明顺序排布成员，成员前按需插入填充；结构体对齐 = 成员最大对齐，
  末尾补齐到对齐的整数倍。
- 填充字节的取值不作规定：解码时记录原样字节，再编码时原样写回；
  用全新值编码时填充为零。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple

__all__ = [
    "uint",
    "struct",
    "array",
    "bitgroup",
    "compute_layout",
    "clear_cache",
    "encode",
    "decode",
    "layout_table",
    "Decoded",
    "UInt",
    "Array",
    "BitGroup",
    "BitField",
    "Member",
    "Struct",
]

_UINT_BITS = (8, 16, 32, 64)
_BIT_ORDERS = ("lsb", "msb")
_ENDIANS = ("little", "big")


# ---------------------------------------------------------------- 描述构造

def uint(bits: int, *, signed: bool = False, endian: str = "little") -> dict:
    """定宽整数。bits ∈ {8,16,32,64}。"""
    if bits not in _UINT_BITS:
        raise ValueError(f"不支持的整数宽度: {bits!r}")
    if endian not in _ENDIANS:
        raise ValueError(f"不支持的端序: {endian!r}")
    return {"kind": "uint", "bits": bits, "signed": bool(signed), "endian": endian}


def struct(name: str, *fields: Tuple[str, dict]) -> dict:
    """结构体。fields 为 (字段名, 类型描述) 序列，按声明顺序布局。"""
    if not name:
        raise ValueError("结构体需要名字")
    seen = set()
    items = []
    for fname, fdesc in fields:
        if fname in seen:
            raise ValueError(f"字段名重复: {fname!r}")
        seen.add(fname)
        items.append([fname, fdesc])
    return {"kind": "struct", "name": name, "fields": items}


def array(of: dict, count: int) -> dict:
    """定长数组，count >= 1。"""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError(f"数组长度必须为正整数: {count!r}")
    return {"kind": "array", "of": of, "count": count}


def bitgroup(storage_bits: int, fields: Tuple[Tuple[str, int], ...], *,
             bitorder: str = "lsb", endian: str = "little") -> dict:
    """位域组：若干位段共享一个 storage_bits 宽的存储单元。

    fields 为 (位段名, 位宽) 序列；bitorder="lsb" 时第一个位段从 bit0 起排，
    "msb" 时第一个位段从最高位起排。位段总宽不得超过存储单元宽度。
    """
    if storage_bits not in _UINT_BITS:
        raise ValueError(f"不支持的位域存储宽度: {storage_bits!r}")
    if bitorder not in _BIT_ORDERS:
        raise ValueError(f"不支持的位序: {bitorder!r}")
    if endian not in _ENDIANS:
        raise ValueError(f"不支持的端序: {endian!r}")
    items = []
    seen = set()
    for fname, width in fields:
        if fname in seen:
            raise ValueError(f"位段名重复: {fname!r}")
        if not isinstance(width, int) or isinstance(width, bool) or width < 1:
            raise ValueError(f"位宽必须为正整数: {width!r}")
        seen.add(fname)
        items.append([fname, width])
    if not items:
        raise ValueError("位域组至少需要一个位段")
    if sum(w for _, w in items) > storage_bits:
        raise ValueError("位段总宽超过存储单元宽度")
    return {
        "kind": "bitgroup",
        "storage_bits": storage_bits,
        "fields": items,
        "bitorder": bitorder,
        "endian": endian,
    }


# ---------------------------------------------------------------- 布局节点

@dataclass(frozen=True)
class UInt:
    bits: int
    signed: bool
    endian: str

    @property
    def size(self) -> int:
        return self.bits // 8

    @property
    def align(self) -> int:
        return self.size


@dataclass(frozen=True)
class Array:
    elem: Any
    count: int

    @property
    def size(self) -> int:
        return self.elem.size * self.count

    @property
    def align(self) -> int:
        return self.elem.align


@dataclass(frozen=True)
class BitField:
    name: str
    bit_offset: int  # 在存储单元内的起始位（bit0 = 最低位）
    width: int


@dataclass(frozen=True)
class BitGroup:
    storage_bits: int
    bitorder: str
    endian: str
    fields: Tuple[BitField, ...]

    @property
    def size(self) -> int:
        return self.storage_bits // 8

    @property
    def align(self) -> int:
        return self.size


@dataclass(frozen=True)
class Member:
    name: str
    offset: int
    padding_before: int
    node: Any


@dataclass(frozen=True)
class Struct:
    name: str
    size: int
    align: int
    tail_padding: int
    members: Tuple[Member, ...]


# ---------------------------------------------------------------- 布局计算（带缓存）

_CACHE: Dict[str, Any] = {}


def _align_up(offset: int, align: int) -> int:
    return (offset + align - 1) // align * align


def _build(desc: Mapping[str, Any]) -> Any:
    kind = desc.get("kind")
    if kind == "uint":
        return UInt(desc["bits"], bool(desc.get("signed", False)),
                    desc.get("endian", "little"))
    if kind == "array":
        return Array(_build(desc["of"]), desc["count"])
    if kind == "bitgroup":
        storage = desc["storage_bits"]
        bitorder = desc.get("bitorder", "lsb")
        bitfields: List[BitField] = []
        pos = 0
        for fname, width in desc["fields"]:
            if bitorder == "lsb":
                bit_offset = pos
            else:  # msb：从最高位往低位排
                bit_offset = storage - pos - width
            bitfields.append(BitField(fname, bit_offset, width))
            pos += width
        return BitGroup(storage, bitorder, desc.get("endian", "little"),
                        tuple(bitfields))
    if kind == "struct":
        offset = 0
        max_align = 1
        members: List[Member] = []
        for fname, fdesc in desc["fields"]:
            node = _build(fdesc)
            aligned = _align_up(offset, node.align)
            members.append(Member(fname, aligned, aligned - offset, node))
            offset = aligned + node.size
            max_align = max(max_align, node.align)
        tail = (-offset) % max_align
        return Struct(desc["name"], offset + tail, max_align, tail,
                      tuple(members))
    raise ValueError(f"未知的类型描述: {desc!r}")


def _cache_key(desc: Mapping[str, Any]) -> str:
    return json.dumps(desc, sort_keys=True, separators=(",", ":"))


def compute_layout(desc: Mapping[str, Any]) -> Any:
    """计算布局。结果按描述的规范化形式缓存：同一描述重复计算返回同一对象。"""
    key = _cache_key(desc)
    node = _CACHE.get(key)
    if node is None:
        node = _build(desc)
        _CACHE[key] = node
    return node


def clear_cache() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------- 编码 / 解码

class Decoded:
    """解码结果：字段值 + 原始字节。交给 encode 可原样写回（含填充字节）。"""

    __slots__ = ("layout", "values", "raw")

    def __init__(self, layout: Struct, values: Dict[str, Any], raw: bytes):
        self.layout = layout
        self.values = values
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"Decoded({self.layout.name}, values={self.values!r})"


def _ensure_layout(desc_or_layout: Any) -> Struct:
    node = desc_or_layout if isinstance(desc_or_layout, Struct) \
        else compute_layout(desc_or_layout)
    if not isinstance(node, Struct):
        raise TypeError("顶层描述必须是结构体")
    return node


def encode(desc_or_layout: Any, values: Any) -> bytes:
    """把字段值编码为字节串。

    values 可以是普通 dict（填充字节写 0），也可以是 decode 返回的 Decoded
    （填充字节与位域保留位原样写回）。
    """
    layout = _ensure_layout(desc_or_layout)
    if isinstance(values, Decoded):
        if values.layout != layout:
            raise ValueError("Decoded 的布局与给定描述不一致")
        buf = bytearray(values.raw)
        field_values = values.values
    else:
        buf = bytearray(layout.size)
        field_values = values
    _encode_struct(layout, field_values, buf, 0)
    return bytes(buf)


def decode(desc_or_layout: Any, data: bytes) -> Decoded:
    """解码字节串。填充字节不参与字段值，但会随 Decoded 保留以便原样写回。"""
    layout = _ensure_layout(desc_or_layout)
    data = bytes(data)
    if len(data) != layout.size:
        raise ValueError(
            f"数据长度 {len(data)} 与布局宽度 {layout.size} 不一致")
    values = _decode_struct(layout, data, 0)
    return Decoded(layout, values, data)


def _encode_struct(node: Struct, values: Mapping[str, Any],
                   buf: bytearray, base: int) -> None:
    for member in node.members:
        if member.name not in values:
            raise ValueError(f"缺少字段: {member.name!r}")
        _encode_node(member.node, values[member.name], buf,
                     base + member.offset)


def _encode_node(node: Any, value: Any, buf: bytearray, base: int) -> None:
    if isinstance(node, UInt):
        _check_int_range(value, node.bits, node.signed)
        buf[base:base + node.size] = int(value).to_bytes(
            node.size, node.endian, signed=node.signed)
        return
    if isinstance(node, Array):
        if len(value) != node.count:
            raise ValueError(
                f"数组长度 {len(value)} 与声明 {node.count} 不一致")
        stride = node.elem.size
        for index, item in enumerate(value):
            _encode_node(node.elem, item, buf, base + index * stride)
        return
    if isinstance(node, BitGroup):
        raw = int.from_bytes(buf[base:base + node.size], node.endian)
        for bitfield in node.fields:
            if bitfield.name not in value:
                raise ValueError(f"缺少位段: {bitfield.name!r}")
            field_value = int(value[bitfield.name])
            if not 0 <= field_value < (1 << bitfield.width):
                raise ValueError(
                    f"位段 {bitfield.name!r} 的值 {field_value} 超出 "
                    f"{bitfield.width} 位范围")
            mask = ((1 << bitfield.width) - 1) << bitfield.bit_offset
            raw = (raw & ~mask) | (field_value << bitfield.bit_offset)
        buf[base:base + node.size] = raw.to_bytes(node.size, node.endian)
        return
    if isinstance(node, Struct):
        _encode_struct(node, value, buf, base)
        return
    raise TypeError(f"未知的布局节点: {node!r}")  # pragma: no cover


def _decode_struct(node: Struct, data: bytes, base: int) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for member in node.members:
        values[member.name] = _decode_node(member.node, data,
                                           base + member.offset)
    return values


def _decode_node(node: Any, data: bytes, base: int) -> Any:
    if isinstance(node, UInt):
        return int.from_bytes(data[base:base + node.size], node.endian,
                              signed=node.signed)
    if isinstance(node, Array):
        stride = node.elem.size
        return [_decode_node(node.elem, data, base + index * stride)
                for index in range(node.count)]
    if isinstance(node, BitGroup):
        raw = int.from_bytes(data[base:base + node.size], node.endian)
        values: Dict[str, int] = {}
        for bitfield in node.fields:
            mask = ((1 << bitfield.width) - 1) << bitfield.bit_offset
            values[bitfield.name] = (raw & mask) >> bitfield.bit_offset
        return values
    if isinstance(node, Struct):
        return _decode_struct(node, data, base)
    raise TypeError(f"未知的布局节点: {node!r}")  # pragma: no cover


def _check_int_range(value: Any, bits: int, signed: bool) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"整数字段需要 int，得到 {type(value).__name__}")
    if signed:
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    else:
        lo, hi = 0, (1 << bits) - 1
    if not lo <= value <= hi:
        raise ValueError(f"值 {value} 超出 {'i' if signed else 'u'}{bits} 范围")


# ---------------------------------------------------------------- 布局表

def layout_table(node: Struct) -> List[Dict[str, Any]]:
    """把布局展开为表格行，便于与手算参考表逐项比对。"""
    rows: List[Dict[str, Any]] = []

    def emit(path: str, offset: int, item: Any, pad_before: int,
             note: str = "") -> None:
        rows.append({
            "path": path,
            "offset": offset,
            "size": item.size,
            "align": item.align,
            "pad_before": pad_before,
            "note": note,
        })

    def walk(item: Any, path: str, offset: int, pad_before: int) -> None:
        note = ""
        if isinstance(item, Struct) and item.tail_padding:
            note = f"末尾补齐 {item.tail_padding}"
        emit(path, offset, item, pad_before, note)
        if isinstance(item, Struct):
            for member in item.members:
                walk(member.node, f"{path}.{member.name}",
                     offset + member.offset, member.padding_before)
        elif isinstance(item, Array):
            rows.append({
                "path": f"{path}[]",
                "offset": offset,
                "size": item.elem.size,
                "align": item.elem.align,
                "pad_before": 0,
                "note": f"stride {item.elem.size} x {item.count}",
            })
            if isinstance(item.elem, Struct):
                for member in item.elem.members:
                    walk(member.node, f"{path}[].{member.name}",
                         offset + member.offset, member.padding_before)
        elif isinstance(item, BitGroup):
            for bitfield in item.fields:
                hi = bitfield.bit_offset + bitfield.width - 1
                rows.append({
                    "path": f"{path}.{bitfield.name}",
                    "offset": offset,
                    "size": 0,
                    "align": 0,
                    "pad_before": 0,
                    "note": f"bits {bitfield.bit_offset}..{hi}",
                })

    walk(node, node.name, 0, 0)
    return rows
