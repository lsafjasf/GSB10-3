"""编号线索：从标题文本中解析编号，映射到层级。

层级约定（固定，写入文档）：
    第X章 / 附录X        -> L1
    a.b.c 多级点分编号    -> L<组件数>（高置信）
    a.b 两位点分编号      -> L2（中置信；无布局佐证且疑似小数时按噪声抑制）
    一、                  -> L2
    （一）/(一)           -> L3
    1. 单个阿拉伯数字     -> L3
    （1）/(1)             -> L4
    ① 圈号               -> L5
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

_CN_NUM = "零一二三四五六七八九十百千两"
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

_RE_CHAPTER = re.compile(rf"^\s*(第[{_CN_NUM}\d]+[章节篇部卷]|附录[{_CN_NUM}\dA-Za-z]*)[、.．:：\s]")
_RE_DOTTED = re.compile(r"^\s*(\d+(?:[.．]\d+)+)(?=[^\d.．]|\s|$)")
_RE_CN_ORD = re.compile(rf"^\s*[{_CN_NUM}]+、")
_RE_PAREN_CN = re.compile(rf"^\s*[（(][{_CN_NUM}]+[）)]")
_RE_ARAB = re.compile(r"^\s*\d{1,2}[.．、](?!\d)")
_RE_PAREN_NUM = re.compile(r"^\s*[（(]\d{1,2}[）)]")
_RE_CIRCLED = re.compile(rf"^\s*[{_CIRCLED}]")

_HIGH, _MED, _LOW = 3.0, 1.5, 1.0


@dataclass
class Clue:
    source: str  # numbering / size / position
    level: int
    weight: float
    detail: str


def _dotted_is_decimal_like(token: str) -> bool:
    """a.b 且 b 为个位数时，疑似小数（如 1.5 倍速）。"""
    parts = token.replace("．", ".").split(".")
    return len(parts) == 2 and len(parts[1]) == 1


def numbering_clue(text: str) -> Tuple[Optional[Clue], bool]:
    """返回 (线索, 是否疑似小数噪声)。无编号时返回 (None, False)。"""
    if _RE_CHAPTER.match(text):
        return Clue("numbering", 1, _HIGH, "编号『第X章/附录』→ L1（高置信）"), False

    m = _RE_DOTTED.match(text)
    if m:
        token = m.group(1)
        depth = token.replace("．", ".").count(".") + 1
        if _dotted_is_decimal_like(token):
            # 中置信：可能是真标题 "2.1 概述"，也可能是小数 "1.5 倍速"
            return Clue("numbering", 2, _MED, f"编号『{token}』→ L2（中置信，疑似小数）"), True
        return Clue("numbering", depth, _HIGH, f"编号『{token}』→ L{depth}（高置信）"), False

    if _RE_CN_ORD.match(text):
        return Clue("numbering", 2, _MED, "编号『一、』→ L2"), False
    if _RE_PAREN_CN.match(text):
        return Clue("numbering", 3, _MED, "编号『（一）』→ L3"), False
    if _RE_ARAB.match(text):
        return Clue("numbering", 3, _MED, "编号『1.』→ L3"), False
    if _RE_PAREN_NUM.match(text):
        return Clue("numbering", 4, _LOW, "编号『（1）』→ L4"), False
    if _RE_CIRCLED.match(text):
        return Clue("numbering", 5, _LOW, "编号『①』→ L5"), False
    return None, False
