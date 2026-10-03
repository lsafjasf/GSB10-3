"""数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Block:
    """一个文本块（通常对应一行/一段）。

    size     字号（pt），None 表示未知
    indent   左缩进（pt），None 表示未知
    centered 是否居中，None 表示未知
    """

    text: str
    size: Optional[float] = None
    indent: Optional[float] = None
    centered: Optional[bool] = None


@dataclass
class Heading:
    """一次标题判定及其修复后的层级。"""

    block_index: int
    text: str
    raw_level: int
    final_level: int
    synthetic: bool = False
    votes: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class Section:
    """章节树节点。start/end 为块区间 [start, end)。"""

    heading: Optional[Heading]
    level: int
    start: int
    end: int
    children: list = field(default_factory=list)

    @property
    def synthetic(self) -> bool:
        return bool(self.heading and self.heading.synthetic)


@dataclass
class JumpRecord:
    """一次层级跳级的处理记录（对照数据的一行）。"""

    block_index: int
    text: str
    raw_level: int
    prev_level: int
    action: str
    final_level: int
    synthetic: bool = False


@dataclass
class SplitResult:
    blocks: list
    headings: list
    suppressed: list
    comparisons: dict
    trees: dict
    intervals: list
    preamble: tuple
    body_size: Optional[float]
    body_indent: Optional[float]
    size_tiers: list
    indent_tiers: list
