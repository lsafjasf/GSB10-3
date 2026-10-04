"""拼音转换核心：分词、单字/词组模式、多音字候选。

优先级规则（candidates 与 pinyin 共用）：
1. 词组模式：输入先按 PHRASE_DICT 做最长匹配；命中词组的字，
   读音以词表为准（词内读音代表上下文判定），优先级最高。
2. 单字模式 / 词外单字：取 CHAR_TABLE 中第 1 个读音，即语料频率
   最高的常见读音。
3. heteronym=True 时返回该字的全部候选；词组模式下，上下文选定的
   读音排在候选第 1 位，其余读音仍按频率降序排列。
"""

from .data_chars import CHAR_TABLE
from .data_phrases import PHRASE_DICT
from .tones import to_tone_mark, to_plain

_PHRASE_MAX_LEN = max(len(word) for word in PHRASE_DICT)
_FORMATTERS = {"tone": to_tone_mark, "plain": to_plain}


def is_hanzi(ch):
    return "一" <= ch <= "鿿"


def segment(text):
    """按词表最长匹配切分。

    返回三元组列表：
      ('phrase', '重庆', ('chong2','qing4'))
      ('char',   '长', None)
      ('raw',    '，！A1', None)   # 连续非汉字整体保留
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not is_hanzi(ch):
            j = i
            while j < n and not is_hanzi(text[j]):
                j += 1
            tokens.append(("raw", text[i:j], None))
            i = j
            continue
        word = None
        for length in range(min(_PHRASE_MAX_LEN, n - i), 1, -1):
            candidate = text[i:i + length]
            if candidate in PHRASE_DICT:
                word = candidate
                break
        if word is not None:
            tokens.append(("phrase", word, PHRASE_DICT[word]))
            i += len(word)
        else:
            tokens.append(("char", ch, None))
            i += 1
    return tokens


def _ordered_readings(ch, preferred):
    readings = list(CHAR_TABLE.get(ch, ()))
    if preferred in readings:
        readings.remove(preferred)
        readings.insert(0, preferred)
    return readings


def _phrase_examples(ch, reading):
    """词表中该字读某音的示例词。"""
    examples = []
    for word, readings in PHRASE_DICT.items():
        if ch in word:
            for idx, word_ch in enumerate(word):
                if word_ch == ch and readings[idx] == reading:
                    examples.append(word)
                    break
    return examples[:3]


def candidates(ch, style="tone"):
    """返回一个多音字的全部候选及优先级依据。

    每个候选为 dict：
      pinyin   该读音（tone/plain 两种形式可分别调用）
      rank     语料频率排名（1 = 最常见）
      basis    排序依据说明
      examples 词表中支持该读音的上下文示例（可能为空）
    """
    if ch not in CHAR_TABLE:
        return []
    formatter = _FORMATTERS[style]
    result = []
    total = len(CHAR_TABLE[ch])
    for rank, reading in enumerate(CHAR_TABLE[ch], 1):
        examples = _phrase_examples(ch, reading)
        if total == 1:
            basis = "单音字，只有一种读音"
        else:
            basis = "语料频率第 %d 常见读音" % rank
        result.append({
            "pinyin": formatter(reading),
            "rank": rank,
            "basis": basis,
            "examples": examples,
        })
    return result


def explain(ch, style="tone"):
    """生成可读的多音字候选排序说明（用于样例展示与 CLI）。"""
    lines = []
    for item in candidates(ch, style=style):
        suffix = ("；词表示例：" + "、".join(item["examples"])) if item["examples"] else ""
        lines.append("%d. %s（%s%s）" % (
            item["rank"], item["pinyin"], item["basis"], suffix))
    return "\n".join(lines)


def pinyin(text, mode="phrase", style="tone", heteronym=False):
    """主转换函数。

    参数
    ----
    text      输入字符串
    mode      'phrase' 词组优先（默认，最长匹配消歧）；
              'char' 单字模式，忽略词表，每字取最高频读音
    style     'tone' 带声调符号；'plain' 不带声调
    heteronym True 时每个汉字位置返回全部候选列表，
              词组模式中上下文读音排第 1；False 时只返回选定读音。

    非汉字字符（拉丁字母、数字、全角/半角标点等）按原样保留，
    连续非汉字合并为一段。字表外汉字也原样保留。
    """
    if style not in _FORMATTERS:
        raise ValueError("style 必须是 'tone' 或 'plain'")
    if mode not in ("phrase", "char"):
        raise ValueError("mode 必须是 'phrase' 或 'char'")
    formatter = _FORMATTERS[style]

    if mode == "char":
        tokens = []
        i = 0
        n = len(text)
        while i < n:
            if is_hanzi(text[i]):
                tokens.append(("char", text[i], None))
                i += 1
            else:
                j = i
                while j < n and not is_hanzi(text[j]):
                    j += 1
                tokens.append(("raw", text[i:j], None))
                i = j
    else:
        tokens = segment(text)

    output = []
    for kind, value, readings in tokens:
        if kind == "raw":
            output.append(value)
            continue
        if kind == "phrase":
            for ch, reading in zip(value, readings):
                if heteronym:
                    output.append([formatter(r) for r in _ordered_readings(ch, reading)])
                else:
                    output.append(formatter(reading))
            continue
        ch = value
        if ch not in CHAR_TABLE:
            output.append(ch)
        elif heteronym:
            output.append([formatter(r) for r in CHAR_TABLE[ch]])
        else:
            output.append(formatter(CHAR_TABLE[ch][0]))
    return output
