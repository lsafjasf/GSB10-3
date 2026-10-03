"""规范文本格式（逐字符可重建）。

每行一个块，制表符分隔 4 个字段：
    文本<TAB>字号<TAB>缩进<TAB>布局标记
- 字号/缩进：数字，未知字段为空；整数不带小数点，浮点保留至多两位小数
- 布局标记：`center`（居中）、`left`（居左）、空（未知）
- 文本内不得含制表符或换行符；行分隔符固定为 \\n，末尾必须有一个 \\n
"""

from __future__ import annotations

from typing import List

from .models import Block


def _fmt_num(value) -> str:
    if value is None:
        return ""
    num = round(float(value), 2)
    if num == int(num):
        return str(int(num))
    return f"{num:.2f}".rstrip("0")


def _parse_num(field: str):
    field = field.strip()
    if field == "":
        return None
    num = float(field)
    return int(num) if num == int(num) else round(num, 2)


def dump_source(blocks: List[Block]) -> str:
    lines = []
    for block in blocks:
        text = block.text
        if "\t" in text or "\n" in text or "\r" in text:
            raise ValueError(f"块文本不允许包含制表符或换行符: {text!r}")
        layout = "center" if block.centered else ("left" if block.centered is False else "")
        lines.append("\t".join([text, _fmt_num(block.size), _fmt_num(block.indent), layout]))
    return "\n".join(lines) + "\n"


def parse_source(content: str) -> List[Block]:
    if not content.endswith("\n"):
        raise ValueError("规范文本必须以 \\n 结尾，以保证逐字符重建")
    body = content[:-1]
    if body == "":
        return []
    blocks = []
    for lineno, line in enumerate(body.split("\n"), start=1):
        parts = line.split("\t")
        if len(parts) != 4:
            raise ValueError(f"第 {lineno} 行字段数不是 4（应为 文本/字号/缩进/布局）")
        text, size_field, indent_field, layout = parts
        if layout not in ("", "center", "left"):
            raise ValueError(f"第 {lineno} 行布局标记非法: {layout!r}")
        centered = None if layout == "" else (layout == "center")
        blocks.append(Block(text=text, size=_parse_num(size_field),
                            indent=_parse_num(indent_field), centered=centered))
    return blocks


def parse_source_file(path: str) -> List[Block]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return parse_source(handle.read())


def dump_source_file(path: str, blocks: List[Block]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(dump_source(blocks))
