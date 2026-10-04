# -*- coding: utf-8 -*-
"""PDF 对象模型（仅依赖标准库）。

约定：
- 名称(Name)内部保留前导 "/"，与字节流写法一致，便于和 str 直接比较。
- Ref(num, gen) 表示间接引用（传统未压缩对象）。
- Ref(num, 0, stream=stmnum, index=k) 还表示“对象流内第 k 个压缩对象”。
- PdfStream 同时携带字典与（未解码 / 已解码）数据。
"""

from dataclasses import dataclass, field


class Name(str):
    """PDF 名称对象，文本形如 "/Type"。"""

    def __repr__(self):
        return "Name(%s)" % str.__repr__(str(self))


@dataclass(frozen=True)
class Ref:
    num: int
    gen: int = 0
    stream: int = -1      # 对象流编号；-1 表示普通未压缩对象
    index: int = -1       # 在对象流 N 数组中的序号（流内序号）

    def is_compressed(self):
        return self.stream >= 0

    def __repr__(self):
        if self.is_compressed():
            return "Ref(%d compressed in %d#%d)" % (self.num, self.stream, self.index)
        return "Ref(%d %d R)" % (self.num, self.gen)


class PdfDict(dict):
    """保持插入顺序的 PDF 字典；键统一为 Name。"""

    def get_name(self, key, default=None):
        k = key if isinstance(key, Name) else Name(key)
        v = self.get(k, None)
        return str(v) if isinstance(v, Name) else default


class PdfStream:
    def __init__(self, dictionary, raw: bytes, decoded: bytes = None):
        self.dict = dictionary if isinstance(dictionary, PdfDict) else PdfDict(dictionary)
        self.raw = raw
        self.decoded = decoded

    def __repr__(self):
        n = len(self.decoded) if self.decoded is not None else len(self.raw)
        return "PdfStream(%d bytes, %r)" % (n, self.dict)


@dataclass
class IndirectObject:
    num: int
    gen: int
    value: object
    offset: int = -1           # "num gen obj" 起点
    section_offset: int = -1   # 由哪一版 xref 段声明（最新段在最前），-1 表示无声明
    header_ok: bool = True     # 偏移处的对象头编号是否与声明一致
    stream_num: int = -1       # 压缩对象所在对象流编号
    stream_index: int = -1     # 流内序号


@dataclass
class ScanRecord:
    """顺序扫描时找到的一处对象定义（增量更新会产生多条历史）。"""
    num: int
    gen: int
    offset: int
    value: object = None
    parse_error: str = None


@dataclass
class PageNode:
    ref: object                # Ref 或 None（内联字典）
    kind: str                  # "Page" / "Pages" / "unknown"
    declared_count: object     # 声明的 /Count（Pages 节点）
    actual_count: int = -1     # 展开到的叶子总数
    kids: int = 0
    inline: bool = False


@dataclass
class Page:
    ordinal: int               # 全局页码，从 1 开始
    num: int                   # 对象全局编号（内联页为 -1）
    gen: int
    in_object_stream: bool
    stream_num: int
    stream_index: int
    node: dict
    inline: bool = False


@dataclass
class XRefEntry:
    num: int
    kind: int                  # 0=在用未压缩, 1=空闲, 2=压缩
    field2: int                # kind0: 偏移; kind1: 下一空闲号; kind2: 对象流号
    gen: int = 0               # kind0/1: 代；kind2: 流内序号
    section_offset: int = -1

    def is_compressed(self):
        return self.kind == 2


@dataclass
class XRefSection:
    offset: int                # 该索引段自身在文件中的位置（xref 关键字或流对象偏移）
    is_stream: bool
    entries: dict = field(default_factory=dict)
    trailer: PdfDict = field(default_factory=PdfDict)
    parse_error: str = None
