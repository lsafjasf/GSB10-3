"""标点符号表：成对符号的开-闭对应、基底分类与全/半角形态。

设计要点
--------
* “基底(base)”表示同一“种”括号。只有同基底的开/闭符号才能配对；
  例如 （ 与 ） 同属 round，可以配对（宽度不同记 width_mismatch），
  而 【 与 〕 基底不同，不会被视为一对。
* ASCII 直单引号 ' 默认不参与配对（撇号歧义），可通过参数开启。
* 「」『』 按括号处理（有方向、可嵌套），不再重复列为引号。
"""

# (开, 闭, 基底名)
BRACKET_PAIRS = (
    ("(", ")", "round"),
    ("（", "）", "round"),
    ("[", "]", "square"),
    ("［", "］", "square"),
    ("{", "}", "curly"),
    ("｛", "｝", "curly"),
    ("<", ">", "angle"),
    ("＜", "＞", "angle"),
    ("【", "】", "square-black"),
    ("〔", "〕", "square-tortoise"),
    ("〖", "〗", "square-white"),
    ("｢", "｣", "brace-corner"),
    ("《", "》", "book-double"),
    ("〈", "〉", "book-single"),
    ("「", "」", "corner"),
    ("『", "』", "corner-white"),
    ("«", "»", "guillemet"),
)

OPEN_TO_CLOSE = {o: c for o, c, _ in BRACKET_PAIRS}
CLOSE_TO_OPEN = {c: o for o, c, _ in BRACKET_PAIRS}
OPEN_CHARS = frozenset(OPEN_TO_CLOSE)
CLOSE_CHARS = frozenset(CLOSE_TO_OPEN)

CHAR_BASE = {}
for _o, _c, _base in BRACKET_PAIRS:
    CHAR_BASE[_o] = _base
    CHAR_BASE[_c] = _base

# 基底 -> 全角形态（用于宽度统一）
FULL_FORMS = {
    "round": ("（", "）"),
    "square": ("［", "］"),
    "curly": ("｛", "｝"),
    "angle": ("＜", "＞"),
    "square-black": ("【", "】"),
    "square-tortoise": ("〔", "〕"),
    "square-white": ("〖", "〗"),
    "brace-corner": ("｢", "｣"),
    "book-double": ("《", "》"),
    "book-single": ("〈", "〉"),
    "corner": ("「", "」"),
    "corner-white": ("『", "』"),
    "guillemet": ("«", "»"),
}

# 基底 -> 半角形态（仅 ASCII 区间有对应物的基底）
HALF_FORMS = {
    "round": ("(", ")"),
    "square": ("[", "]"),
    "curly": ("{", "}"),
    "angle": ("<", ">"),
}

_HALF_CHARS = frozenset("()[]{}<>")


def is_full_width_bracket(ch: str) -> bool:
    """括号字符是否为全角形态。"""
    return ch in CHAR_BASE and ch not in _HALF_CHARS


# 引号类（有方向，独立栈匹配）
QUOTE_PAIRS = (
    ("“", "”"),
    ("‘", "’"),
)
QUOTE_OPEN_TO_CLOSE = dict(QUOTE_PAIRS)
QUOTE_CLOSE_TO_OPEN = {c: o for o, c in QUOTE_PAIRS}
QUOTE_OPEN_CHARS = frozenset(QUOTE_OPEN_TO_CLOSE)
QUOTE_CLOSE_CHARS = frozenset(QUOTE_CLOSE_TO_OPEN)

STRAIGHT_DOUBLE = '"'
STRAIGHT_SINGLE = "'"
