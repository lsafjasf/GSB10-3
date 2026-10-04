"""声调处理：数字声调 <-> 声调符号 <-> 无声调。

内部数据一律用数字声调存储（如 "zhong1"、"de5"），
输出时按规则转换为带声调符号或无声调形式。

标调规则（与 tests 中的手写规则用例逐项对拍）：
1. 有 a 标 a，没 a 找 o，没 o 找 e；
2. iu / ui 同时出现时，标在第二个元音上（liu2 -> liú，shui3 -> shuǐ）；
3. 单韵母直接标在该元音上（含 ü）；
4. 轻声（5 或 0）不标调。
"""

_TONE_MARKS = {
    "a": "āáǎà",
    "o": "ōóǒò",
    "e": "ēéěè",
    "i": "īíǐì",
    "u": "ūúǔù",
    "ü": "ǖǘǚǜ",
}

# 主元音优先级：a > o > e
_MAIN_VOWELS = ("a", "o", "e")


def split_tone(syllable):
    """把 'zhong1' 拆成 ('zhong', 1)；无数字尾视为轻声。"""
    if syllable and syllable[-1].isdigit():
        return syllable[:-1], int(syllable[-1])
    return syllable, 5


def _mark_index(base):
    """按标调规则找出应标调的元音下标，找不到返回 -1。"""
    for vowel in _MAIN_VOWELS:
        pos = base.find(vowel)
        if pos != -1:
            return pos
    for pair in ("iu", "ui"):
        pos = base.find(pair)
        if pos != -1:
            return pos + 1
    for pos, ch in enumerate(base):
        if ch in _TONE_MARKS:
            return pos
    return -1


def to_tone_mark(syllable):
    """'zhong1' -> 'zhōng'；轻声原样返回。"""
    base, tone = split_tone(syllable)
    if tone in (0, 5):
        return base
    idx = _mark_index(base)
    if idx == -1:
        return base
    ch = base[idx]
    return base[:idx] + _TONE_MARKS[ch][tone - 1] + base[idx + 1:]


def to_plain(syllable):
    """'zhong1' -> 'zhong'（去掉声调数字，保留 ü）。"""
    base, _ = split_tone(syllable)
    return base
