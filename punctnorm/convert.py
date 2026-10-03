"""上下文感知的全角 / 半角规范化。

转换策略
--------
1. 先识别"必须保持西文形态"的受保护片段，片段内一律转半角：
   - 数字：``３．１４`` / ``1,000`` / ``１２：３０``；
   - 数字+单位：``５ｋｇ`` / ``3ＧＢ``（字母紧跟数字视为单位）；
   - 英文缩写：``Ｕ．Ｓ．Ａ．`` / ``e.g.``（单字母加点重复出现）。
2. 其余全角字母/数字按片段判断：
   - 连续多个（如 ``Ｈｅｌｌｏ``、``１２３``）或与字母数字相邻 -> 半角；
   - 单个全角字母夹在中文之间（如 ``第Ａ章``）-> 保留全角。
3. 标点看上下文：
   - 半角标点邻接中文 -> 全角（``,``→``，``、句末 ``.``→``。``、
     括号内容为中文 -> 全角括号），但数字/缩写内部的点号不动；
   - 全角标点夹在西文之间 -> 半角（``hello，world`` -> ``hello,world``）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---- 字符分类 ---------------------------------------------------------------

_FW0, _FW1 = ord("！"), ord("～")  # ！=FF01, ～=FF5E，对应 !..~


def is_fullwidth_ascii(ch: str) -> bool:
    return len(ch) == 1 and _FW0 <= ord(ch) <= _FW1 or ch == "　"


def to_half(ch: str) -> str:
    o = ord(ch)
    if _FW0 <= o <= _FW1:
        return chr(o - 0xFEE0)
    if ch == "　":
        return " "
    return ch


def is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return (
        0x3000 <= o <= 0x303F or       # CJK 标点
        0x3040 <= o <= 0x30FF or       # 平假名 / 片假名
        0x3400 <= o <= 0x4DBF or
        0x4E00 <= o <= 0x9FFF or
        0xF900 <= o <= 0xFAFF or
        0xFF65 <= o <= 0xFF9F or       # 半角片假名
        0xAC00 <= o <= 0xD7AF          # 谚文
    )


def _is_alnum(ch: str) -> bool:
    if not ch:
        return False
    if ch.isascii() and ch.isalnum():
        return True
    return is_fullwidth_ascii(ch) and to_half(ch).isalnum()


def _is_western(ch: str) -> bool:
    return _is_alnum(ch)


# 半角 -> 全角 标点表
HALF_TO_FULL = {
    ",": "，", ".": "。", "?": "？", "!": "！",
    ":": "：", ";": "；",
    "(": "（", ")": "）",
    "[": "［", "]": "］",
    "{": "｛", "}": "｝",
}
FULL_TO_HALF_PUNCT = {
    "，": ",", "．": ".", "！": "!", "？": "?",
    "：": ":", "；": ";",
    "（": "(", "）": ")",
    "［": "[", "］": "]",
    "｛": "{", "｝": "}",
}
_BRACKET_OPEN = {"(": ")", "[": "]", "{": "}", "（": "）", "［": "］", "｛": "｝"}
_BRACKET_CLOSE = {v: k for k, v in _BRACKET_OPEN.items()}

_D = r"[0-9０-９]"
# 数字（含小数点 / 千分位 / 时分号）
_NUM_RE = re.compile(rf"{_D}+(?:[.,．，:：]{_D}+)+[%％]?")
# 数字 + 紧贴的单位字母（kg、GB、5G 等）
_UNIT_RE = re.compile(rf"{_D}+[A-Za-zＡ-ｚ]+")
# 英文缩写：单字母加点重复 >=2 次（U.S.A. / e.g. / Ｕ．Ｓ．Ａ．）
_ABBR_RE = re.compile(r"(?:[A-Za-zＡ-ｚ][.．]){2,}[A-Za-zＡ-ｚ]?[.．]?")


@dataclass
class Change:
    pos: int
    before: str
    after: str
    reason: str

    def as_row(self) -> tuple:
        return (self.pos, self.before, self.after, self.reason)


def _protected_spans(text: str) -> dict:
    """返回 {字符下标: 转换原因}，这些位置强制半角。"""
    spans = {}

    def claim(m: re.Match, reason: str) -> None:
        for j in range(m.start(), m.end()):
            spans.setdefault(j, reason)

    for m in _NUM_RE.finditer(text):
        claim(m, "数字（小数点/千分位/时间）")
    for m in _UNIT_RE.finditer(text):
        claim(m, "数字+单位")
    for m in _ABBR_RE.finditer(text):
        claim(m, "英文缩写")
    return spans


def _sig_neighbor(text: str, i: int, step: int):
    """沿 step 方向找第一个非空白字符。"""
    j = i + step
    while 0 <= j < len(text) and text[j] in " \t　":
        j += step
    return text[j] if 0 <= j < len(text) else ""


def diff_width(text: str) -> list:
    """返回每个发生转换字符的 Change 列表（转换前后对照数据）。"""
    protected = _protected_spans(text)
    changes: list[Change] = []

    # 预计算全角字母数字"片段"的处理决定（连续片段只算一次）
    run_half = set()
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if is_fullwidth_ascii(ch) and to_half(ch).isalnum() and i not in protected:
            j = i
            while (j < n and is_fullwidth_ascii(text[j])
                   and to_half(text[j]).isalnum()):
                j += 1
            left, right = _sig_neighbor(text, i, -1), _sig_neighbor(text, j, 1)
            if (j - i) >= 2 or _is_alnum(left) or _is_alnum(right):
                run_half.update(range(i, j))
            i = j
        else:
            i += 1

    bracket_stack = []  # [(原字符, 是否转成全角)]
    for i, ch in enumerate(text):
        # ---- 受保护片段：全角一律转半角 ----
        if i in protected and is_fullwidth_ascii(ch):
            half = to_half(ch)
            if half != ch:
                changes.append(Change(i, ch, half, protected[i]))
            continue

        # ---- 全角 ASCII ----
        if is_fullwidth_ascii(ch) and ch != "　":
            half = to_half(ch)
            if half.isalnum():
                if i in run_half:
                    changes.append(Change(i, ch, half, "连续/邻接字母数字"))
                # 否则：夹在中文中的单个字母，保留全角
                continue

            # 全角标点
            if ch in FULL_TO_HALF_PUNCT:
                if ch in _BRACKET_OPEN:
                    nxt = _sig_neighbor(text, i, 1)
                    prv = _sig_neighbor(text, i, -1)
                    if _is_western(nxt) and _is_western(prv):
                        changes.append(Change(i, ch, half, "西文语境括号"))
                        bracket_stack.append((ch, True))
                    else:
                        bracket_stack.append((ch, False))
                elif ch in _BRACKET_CLOSE:
                    converted = False
                    if bracket_stack and bracket_stack[-1][0] == _BRACKET_CLOSE[ch]:
                        converted = bracket_stack.pop()[1]
                    if converted:
                        changes.append(Change(i, ch, half, "与开括号配对（西文语境）"))
                else:
                    prv = _sig_neighbor(text, i, -1)
                    nxt = _sig_neighbor(text, i, 1)
                    if _is_western(prv) and _is_western(nxt):
                        changes.append(Change(i, ch, half, "夹在西文之间"))
            continue

        # ---- 半角标点的中文化 ----
        if ch in HALF_TO_FULL:
            if ch in _BRACKET_OPEN:
                nxt = _sig_neighbor(text, i, 1)
                convert = is_cjk(nxt)
                if convert:
                    changes.append(Change(i, ch, HALF_TO_FULL[ch], "邻接中文（括号内容）"))
                bracket_stack.append((ch, convert))
                continue
            if ch in _BRACKET_CLOSE:
                convert = is_cjk(_sig_neighbor(text, i, -1))
                if bracket_stack and bracket_stack[-1][0] == _BRACKET_CLOSE[ch]:
                    convert = bracket_stack.pop()[1] or convert
                if convert:
                    changes.append(Change(i, ch, HALF_TO_FULL[ch], "邻接中文/与开括号配对"))
                continue
            prv = _sig_neighbor(text, i, -1)
            nxt = _sig_neighbor(text, i, 1)
            if not is_cjk(prv):
                continue
            if ch == ".":
                # 句末点号 -> 。；不处理数字（已在受保护片段）
                if nxt == "" or is_cjk(nxt) or nxt in " \t":
                    changes.append(Change(i, ch, "。", "中文句末点号"))
            else:
                changes.append(Change(i, ch, HALF_TO_FULL[ch], "邻接中文"))

    return changes


def normalize_width(text: str) -> str:
    changes = diff_width(text)
    out = list(text)
    for c in changes:
        out[c.pos] = c.after
    return "".join(out)
