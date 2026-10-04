# -*- coding: utf-8 -*-
"""拼音转换核心：单字/词组两种模式，多音字候选，三种声调风格。"""

from .data import CHAR_TABLE, PHRASE_TABLE

MAX_PHRASE_LEN = max(len(p) for p in PHRASE_TABLE)

_TONE_MARKS = {
    'a': 'āáǎà',
    'e': 'ēéěè',
    'i': 'īíǐì',
    'o': 'ōóǒò',
    'u': 'ūúǔù',
    'ü': 'ǖǘǚǜ',
}


def _mark_index(base):
    """按标调规则找应标调的元音下标：a/e 优先，ou 标 o，其余标最后一个元音。"""
    if 'a' in base:
        return base.index('a')
    if 'e' in base:
        return base.index('e')
    if 'ou' in base:
        return base.index('o')
    for i in range(len(base) - 1, -1, -1):
        if base[i] in 'iouü':
            return i
    return -1


def to_tone_marks(numbered):
    """'hang2' -> 'háng'；轻声（5）不标调。"""
    if not numbered or not numbered[-1].isdigit():
        return numbered
    tone = int(numbered[-1])
    base = numbered[:-1]
    if tone == 5:
        return base
    idx = _mark_index(base)
    if idx < 0:
        return base
    ch = base[idx]
    return base[:idx] + _TONE_MARKS[ch][tone - 1] + base[idx + 1:]


def strip_tone(numbered):
    """'hang2' -> 'hang'。"""
    if numbered and numbered[-1].isdigit():
        return numbered[:-1]
    return numbered


def _format(numbered, style):
    if style == 'marks':
        return to_tone_marks(numbered)
    if style == 'numbers':
        return numbered
    if style == 'plain':
        return strip_tone(numbered)
    raise ValueError("style 必须是 'marks' / 'numbers' / 'plain'")


def normalize_fullwidth(text):
    """全角 ASCII 变体（Ａ-Ｚ ａ-ｚ ０-９ 及全角符号）转半角；中文标点（。、等）保留。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(' ')
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return ''.join(out)


def _segment(text):
    """词组模式：对词表做从左到右最长匹配；未命中回退单字；非汉字原样成块。"""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in CHAR_TABLE:
            hit = None
            for size in range(min(MAX_PHRASE_LEN, n - i), 1, -1):
                piece = text[i:i + size]
                if piece in PHRASE_TABLE:
                    hit = piece
                    break
            if hit is not None:
                tokens.append(('phrase', hit))
                i += len(hit)
            else:
                tokens.append(('char', ch))
                i += 1
        else:
            j = i
            while j < n and text[j] not in CHAR_TABLE:
                j += 1
            tokens.append(('raw', text[i:j]))
            i = j
    return tokens


def _tokens(text, mode):
    if mode == 'phrase':
        return _segment(text)
    if mode == 'char':
        return [('char', c) if c in CHAR_TABLE else ('raw', c) for c in text]
    raise ValueError("mode 必须是 'phrase' / 'char'")


def convert(text, style='marks', mode='phrase', normalize=False):
    """返回 token 列表：每个汉字一个拼音，非汉字原样成块保留。

    style: 'marks' 带声调符号 / 'numbers' 数字声调 / 'plain' 不带声调
    mode:  'phrase' 词组优先（最长匹配词表）/ 'char' 逐字取默认音
    normalize: True 时先把全角 ASCII 变体转半角
    词表未收录的汉字原样保留（不猜测读音）。
    """
    if normalize:
        text = normalize_fullwidth(text)
    out = []
    for kind, tok in _tokens(text, mode):
        if kind == 'raw':
            out.append(tok)
        elif kind == 'phrase':
            out.extend(_format(r, style) for r in PHRASE_TABLE[tok])
        else:
            out.append(_format(CHAR_TABLE[tok][0], style))
    return out


def convert_str(text, sep=' ', **kwargs):
    """convert 的字符串便捷形式，用 sep 连接各 token。"""
    return sep.join(convert(text, **kwargs))


def heteronym(text, style='marks', mode='phrase'):
    """逐字返回候选列表。词组命中时该字候选收敛为词表定音（单元素列表）。"""
    out = []
    for kind, tok in _tokens(text, mode):
        if kind == 'raw':
            out.append([tok])
        elif kind == 'phrase':
            out.extend([_format(r, style)] for r in PHRASE_TABLE[tok])
        else:
            out.append([_format(r, style) for r in CHAR_TABLE[tok]])
    return out


def candidates(ch, style='marks'):
    """单字候选读音，按优先级排序，附排序依据与词表例证。"""
    if ch not in CHAR_TABLE:
        return []
    readings = CHAR_TABLE[ch]
    result = []
    for rank, numbered in enumerate(readings, 1):
        examples = [p for p, rs in PHRASE_TABLE.items()
                    if ch in p and rs[p.index(ch)] == numbered]
        result.append({
            'char': ch,
            'pinyin': _format(numbered, style),
            'numbered': numbered,
            'rank': rank,
            'basis': ('现代汉语语料读音频率第 %d 位' % rank)
                     + ('；词表例证：' + '、'.join(examples[:4]) if examples else ''),
            'examples': examples,
        })
    return result


def explain(text, style='marks'):
    """逐字给出定音结果与依据（词组规则 or 单字默认频率）。"""
    out = []
    for kind, tok in _tokens(text, 'phrase'):
        if kind == 'raw':
            out.append({'text': tok, 'type': 'raw', 'pinyin': None,
                        'reason': '非汉字或词表未收录，原样保留'})
        elif kind == 'phrase':
            readings = PHRASE_TABLE[tok]
            for c, r in zip(tok, readings):
                out.append({'text': c, 'type': 'phrase', 'pinyin': _format(r, style),
                            'reason': '命中词组「%s」规则' % tok})
        else:
            out.append({'text': tok, 'type': 'char',
                        'pinyin': _format(CHAR_TABLE[tok][0], style),
                        'reason': '单字默认音（语料频率第 1 位）'})
    return out
