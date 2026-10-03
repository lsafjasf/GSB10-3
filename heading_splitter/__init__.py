"""章节切分与层级还原（只依赖标准库）。

公开接口：
- Block / Section / SplitResult：数据模型
- split_document：三路线索融合 + 跳级修复 + 章节切分
- parse_source / dump_source：规范文本格式，支持逐字符重建
- FILL / DEMOTE：两种跳级修复策略
"""

from .models import Block, Section, SplitResult, Heading, JumpRecord
from .splitter import split_document, rebuild_source, FILL, DEMOTE
from .source import parse_source, dump_source, parse_source_file, dump_source_file
from .report import render_report, render_comparison

__all__ = [
    "Block",
    "Section",
    "SplitResult",
    "Heading",
    "JumpRecord",
    "split_document",
    "rebuild_source",
    "FILL",
    "DEMOTE",
    "parse_source",
    "dump_source",
    "parse_source_file",
    "dump_source_file",
    "render_report",
    "render_comparison",
]
