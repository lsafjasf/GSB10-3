"""lzdict：面向短消息的 LZ77 压缩器，支持从样本训练静态字典。"""

from .codec import (
    compress,
    decompress,
    LZDictError,
    DictionaryNotFoundError,
    CorruptDataError,
    IntegrityError,
)
from .dictionary import train_dictionary, dict_id_for, DictStore

__all__ = [
    "compress",
    "decompress",
    "train_dictionary",
    "dict_id_for",
    "DictStore",
    "LZDictError",
    "DictionaryNotFoundError",
    "CorruptDataError",
    "IntegrityError",
]
