"""纯标准库实现的拼音转换库：词组消歧 + 多音字候选 + 声调转换。"""

from .tones import to_tone_mark, to_plain
from .core import pinyin, candidates, explain, segment, is_hanzi
from .data_chars import CHAR_TABLE
from .data_phrases import PHRASE_DICT

__all__ = [
    "pinyin",
    "candidates",
    "explain",
    "segment",
    "is_hanzi",
    "to_tone_mark",
    "to_plain",
    "CHAR_TABLE",
    "PHRASE_DICT",
]
