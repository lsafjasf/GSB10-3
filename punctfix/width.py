"""上下文感知的全角/半角规范化。

转换规则（全部可从返回的 changes 中核对，rule 字段标注命中规则）
----------------------------------------------------------------
R1 digit  全角数字 ０-９ 一律转半角（现代中文排版数字用半角）。
R2 latin  全角拉丁字母 Ａ-Ｚａ-ｚ 一律转半角（全角拉丁基本都是误输入）。
R3 dot    全角句点 ． 左侧（跳过空白后）是 ASCII 字母/数字时转半角，
          覆盖 ３．１４、Ｕ．Ｓ．Ａ． 等；两侧都是 CJK 时保留全角。
R4 num    全角 ASCII 标点（括号除外）按两类处理：
          数学/单位符号（％＋－／＝等）紧邻数字（跳过空白）或处于
          ASCII 之间即转半角；句读符号（，：；！？）只有左右紧邻
          都是 ASCII 才转，中文语境保留全角（名，12:30 的逗号保留）。
          例外：～ 处在两个数字之间时保留全角（中文数字区间习惯：
          １～１０ => 1～10）。
R5 space  全角空格 U+3000 转半角；行首缩进连续的 U+3000 保留（缩进上下文）。
R6 unit   数字后紧跟已知单位字母时，数字与单位之间补一个半角空格
          （５ｋｇ => 5 kg；单位字母本身由 R2 转半角）。

不处理的内容
------------
* 括号/引号字符一律不在本模块改动，交给配对模块按配对关系统一宽度；
* CJK 标点 。、，（中文语境）等按规则保留；
* ℃、° 等无半角对应的符号保持原样。

另提供不做上下文判断的 :func:`half_to_full_ascii`，便于需要反向转换
或做对照数据的场景（1:1 映射，U+FF01–FF5E）。
"""

from dataclasses import dataclass
from typing import List, Tuple

# ---------------------------------------------------------------- 映射

FW_DIGITS = "０１２３４５６７８９"
FW_UPPER = "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
FW_LOWER = "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"

_DIGIT_MAP = {c: str(i) for i, c in enumerate(FW_DIGITS)}
_UPPER_MAP = {c: chr(ord("A") + i) for i, c in enumerate(FW_UPPER)}
_LOWER_MAP = {c: chr(ord("a") + i) for i, c in enumerate(FW_LOWER)}
ALWAYS_MAP = {**_DIGIT_MAP, **_UPPER_MAP, **_LOWER_MAP}

# 括号/引号交给配对模块，这里绝不碰
_RESERVED = frozenset("（）［］｛｝＜＞【】〔〕〖〗｢｣《》〈〉「」『』«»“”‘’")

# R3/R4 涉及的全角 ASCII 标点
FW_FULL_STOP = "．"  # U+FF0E
FW_RANGE = "～"      # U+FF5E

# 数学/单位类标点：紧邻数字即可半角化
MATH_PUNCT = frozenset("％＋－／＝＊＾＆＠＃＄｜＼｀＇＂＿")
# 句读类标点：左右（跳过空白）都是 ASCII 才半角化，中文语境保留全角
SENTENCE_PUNCT = frozenset("，：；！？")

# 数字后常见单位（小写匹配，不改变原大小写）
UNITS = frozenset("""
kg g mg ug t km m cm mm um nm μm in ft yd mi
l ml dl kl s ms us ns min h hz khz mhz ghz
v mv kv a ma ka w kw mw wh kwh j kj pa kpa mpa n
b kb mb gb tb pb px pt em rem dpi rpm mol mol/l ppm db dbm
c f k
""".split())


@dataclass
class Change:
    line: int       # 1-based
    pos: int        # 0-based，行内位置
    before: str
    after: str
    rule: str
    detail: str = ""

    def as_row(self) -> Tuple:
        return (self.line, self.pos, self.before, self.after, self.rule, self.detail)


# ---------------------------------------------------------------- 判定

def _neighbor(line: str, i: int, step: int) -> str:
    """取 i 向 step 方向跳过空白后的第一个字符；越界返回空串。"""
    j = i + step
    while 0 <= j < len(line) and line[j] in (" ", "　", "\t"):
        j += step
    return line[j] if 0 <= j < len(line) else ""


def _is_ascii_alnum(ch: str) -> bool:
    return bool(ch) and ord(ch) < 128 and (ch.isalnum())


def _is_ascii(ch: str) -> bool:
    return bool(ch) and ord(ch) < 128 and not ch.isspace()


def _is_digit(ch: str) -> bool:
    return bool(ch) and ("0" <= ch <= "9" or ch in _DIGIT_MAP)


# ---------------------------------------------------------------- 主流程

def normalize_line(line: str, line_no: int = 1) -> Tuple[str, List[Change]]:
    changes: List[Change] = []
    chars = list(line)

    # --- Pass 1: R1/R2 数字与字母（必转）
    for i, ch in enumerate(chars):
        if ch in ALWAYS_MAP:
            new = ALWAYS_MAP[ch]
            rule = "digit" if ch in _DIGIT_MAP else "latin"
            chars[i] = new
            changes.append(Change(line_no, i, ch, new, rule))

    text = "".join(chars)

    # --- Pass 2: R3/R4 标点（依赖 pass1 后的 ASCII 上下文）
    out = list(text)
    for i, ch in enumerate(text):
        if ch in _RESERVED or ord(ch) < 0xFF01 or ord(ch) > 0xFF5E:
            continue
        half = chr(ord(ch) - 0xFEE0)

        if ch == FW_FULL_STOP:
            left = _neighbor(text, i, -1)
            if _is_ascii_alnum(left):
                out[i] = half
                changes.append(Change(line_no, i, ch, half, "dot",
                                      "左侧为 ASCII 字母/数字"))
            continue

        if ch == FW_RANGE:
            l = _neighbor(text, i, -1)
            r = _neighbor(text, i, 1)
            if _is_digit(l) and _is_digit(r):
                continue  # 中文区间习惯保留 ～
            if _is_ascii(l) and _is_ascii(r):
                out[i] = half
                changes.append(Change(line_no, i, ch, half, "num",
                                      "ASCII 上下文"))
            continue

        l = _neighbor(text, i, -1)
        r = _neighbor(text, i, 1)
        ascii_ctx = _is_ascii(l) and _is_ascii(r)
        if ch in MATH_PUNCT:
            hit = _is_digit(l) or _is_digit(r) or ascii_ctx
        else:  # 句读类：必须两侧都是 ASCII
            hit = ascii_ctx
        if hit:
            out[i] = half
            why = "数字/数学符号上下文" if ch in MATH_PUNCT else "两侧均为 ASCII"
            changes.append(Change(line_no, i, ch, half, "num", why))

    text = "".join(out)

    # --- Pass 3: R5 全角空格（保留行首缩进）
    out = list(text)
    indent_end = 0
    while indent_end < len(out) and out[indent_end] == "　":
        indent_end += 1
    for i, ch in enumerate(text):
        if ch == "　" and i >= indent_end:
            out[i] = " "
            changes.append(Change(line_no, i, "　", " ", "space",
                                  "行首缩进外的全角空格"))
    text = "".join(out)

    # --- Pass 4: R6 数字-单位间距
    out = list(text)
    inserts: List[Tuple[int, str]] = []
    n = len(text)
    i = 0
    while i < n:
        if "0" <= text[i] <= "9":
            j = i
            while j < n and (text[j].isdigit() or text[j] in ".,"):
                j += 1
            k = j
            while k < n and text[k].isalpha():
                k += 1
            word = text[j:k].lower()
            if k > j and word in UNITS:
                # 已知单位，且数字与单位间没有空白 => 补一个半角空格
                if j > 0 and text[j - 1] != " ":
                    inserts.append((j, " "))
                    changes.append(Change(
                        line_no, j, "", " ", "unit",
                        f"数字与单位 {text[j:k]} 间补空格"))
            i = max(k, i + 1)
        else:
            i += 1
    for pos, s in reversed(inserts):
        out.insert(pos, s)
    return "".join(out), changes


def normalize_text(text: str) -> Tuple[str, List[Change]]:
    if text == "":
        return "", []
    parts: List[str] = []
    all_changes: List[Change] = []
    for idx, line in enumerate(text.split("\n")):
        new, ch = normalize_line(line, idx + 1)
        parts.append(new)
        all_changes.extend(ch)
    return "\n".join(parts), all_changes


# ---------------------------------------------------------------- 反向工具

FULL_ASCII_BLOCK = {chr(i): chr(i + 0xFEE0)
                    for i in range(0x21, 0x7F)
                    if chr(i) not in "()[]{}<>"}  # 括号避免与 CJK 括号歧义


def half_to_full_ascii(text: str) -> str:
    """不加上下文判断的 1:1 反向映射（括号不映射，避免与 CJK 括号冲突）。"""
    return "".join(FULL_ASCII_BLOCK.get(ch, ch) for ch in text)
