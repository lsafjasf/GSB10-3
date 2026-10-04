# -*- coding: utf-8 -*-
"""纯标准库拼音转换库：词组优先、多音字候选、带调/不带调输出。"""

from .core import (
    convert,
    convert_str,
    heteronym,
    candidates,
    explain,
    normalize_fullwidth,
    to_tone_marks,
    strip_tone,
)
from .data import CHAR_TABLE, PHRASE_TABLE

__all__ = [
    'convert', 'convert_str', 'heteronym', 'candidates', 'explain',
    'normalize_fullwidth', 'to_tone_marks', 'strip_tone',
    'CHAR_TABLE', 'PHRASE_TABLE',
]
__version__ = '1.0.0'
