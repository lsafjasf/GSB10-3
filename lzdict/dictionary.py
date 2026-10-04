"""字典训练与持久化。

训练思路（类似 zstd 的 dictionary 概念，实现为纯 Python）：
    1. 统计所有样本中长度 [min_len, max_len] 的子串的"文档频率"
       （同一子串在单条样本内只计一次，避免单条重复内容刷分）；
    2. 估计每个候选子串的净收益 = (df - 1) * (len - 3) - len，
       即每次复用省下的字面量字节减去 token 开销与字典自身存储成本；
    3. 按收益贪心选取互不包含的候选，拼接为字典内容，上限 max_size。

持久化与兼容性策略：
    - 字典内容以 ``<dict_id>.dict`` 文件存入 DictStore 目录；
    - dict_id 是内容的 sha256 前 16 位十六进制，内容变则 id 变，
      重新训练只会"新增"字典文件，绝不覆盖旧文件；
    - 每个压缩块头部都记录压缩时使用的 dict_id，解压时按 id 取字典，
      因此只要保留旧字典文件，旧数据永远可以解压（向后兼容）。
"""

from __future__ import annotations

import hashlib
import os
import re

DICT_MAGIC = b"DZD1"
DICT_ID_LEN = 16
_DEFAULT_MAX_SIZE = 16 * 1024


def dict_id_for(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:DICT_ID_LEN]


def train_dictionary(
    samples,
    max_size: int = _DEFAULT_MAX_SIZE,
    min_len: int = 4,
    max_len: int = 24,
    min_df: int = 2,
    max_candidates: int = 8000,
) -> bytes:
    """从样本（bytes/str 的可迭代对象）训练字典，返回字典内容字节。

    样本为空或样本间没有重复子串时返回 b""（空字典，合法且可用）。
    """
    normalized = []
    for sample in samples:
        if isinstance(sample, str):
            sample = sample.encode("utf-8")
        normalized.append(bytes(sample))

    # 第一遍：统计 min_len 短串的文档频率，用于剪枝。
    short_df: dict[bytes, int] = {}
    for sample in normalized:
        seen = set()
        limit = len(sample) - min_len + 1
        for i in range(max(0, limit)):
            key = sample[i:i + min_len]
            if key not in seen:
                seen.add(key)
                short_df[key] = short_df.get(key, 0) + 1

    hot = {key for key, count in short_df.items() if count >= min_df}
    if not hot:
        return b""

    # 第二遍：只对"短串足够热"的位置扩展更长的候选。
    df: dict[bytes, int] = {}
    for sample in normalized:
        seen = set()
        limit = len(sample) - min_len + 1
        for i in range(max(0, limit)):
            if sample[i:i + min_len] not in hot:
                continue
            upper = min(len(sample), i + max_len)
            for j in range(i + min_len, upper + 1):
                key = sample[i:j]
                if key not in seen:
                    seen.add(key)
                    df[key] = df.get(key, 0) + 1

    candidates = []
    for sub, count in df.items():
        length = len(sub)
        if count < min_df:
            continue
        # 每次复用：省 length 个字面量，花约 3 字节 token；字典存储花 length。
        saving = (count - 1) * (length - 3) - length
        if saving > 0:
            candidates.append((saving, sub))
    candidates.sort(key=lambda item: -item[0])
    del candidates[max_candidates:]

    selected: list[bytes] = []
    used = 0
    for saving, sub in candidates:
        length = len(sub)
        if used + length > max_size:
            continue
        if any(sub in chosen or chosen in sub for chosen in selected):
            continue
        selected.append(sub)
        used += length

    # 高频短串放后面（离数据更近，偏移更小），长串放前面。
    selected.sort(key=len)
    return b"".join(selected)


class DictStore:
    """字典的磁盘仓库：按 dict_id 存取，只增不改，保证旧数据可读。"""

    _SAFE_ID = re.compile(r"^[0-9a-f]{%d}$" % DICT_ID_LEN)

    def __init__(self, path: str):
        self.path = path
        os.makedirs(path, exist_ok=True)

    def _file(self, dict_id: str) -> str:
        if not self._SAFE_ID.match(dict_id):
            raise ValueError(f"非法 dict_id: {dict_id!r}")
        return os.path.join(self.path, dict_id + ".dict")

    def save(self, content: bytes) -> str:
        """保存字典内容，返回 dict_id。幂等：同内容重复保存不产生变化。"""
        dict_id = dict_id_for(content)
        target = self._file(dict_id)
        if not os.path.exists(target):
            tmp = target + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(DICT_MAGIC)
                fh.write(content)
            os.replace(tmp, target)
        return dict_id

    def load(self, dict_id: str) -> bytes:
        """按 id 读取字典内容；不存在时抛 DictionaryNotFoundError。"""
        from .codec import DictionaryNotFoundError, CorruptDataError

        target = self._file(dict_id)
        if not os.path.exists(target):
            raise DictionaryNotFoundError(f"字典 {dict_id} 不存在于 {self.path}")
        with open(target, "rb") as fh:
            raw = fh.read()
        if not raw.startswith(DICT_MAGIC):
            raise CorruptDataError(f"字典文件 {target} 格式非法")
        content = raw[len(DICT_MAGIC):]
        if dict_id_for(content) != dict_id:
            raise CorruptDataError(f"字典文件 {target} 内容与 id 不符（已损坏）")
        return content

    def exists(self, dict_id: str) -> bool:
        return os.path.exists(self._file(dict_id))

    def list_ids(self) -> list[str]:
        ids = []
        for name in os.listdir(self.path):
            stem, dot, suffix = name.rpartition(".")
            if suffix == "dict" and self._SAFE_ID.match(stem):
                ids.append(stem)
        return sorted(ids)

    def latest_id(self):
        """返回最近写入的 dict_id（按文件修改时间），没有则返回 None。"""
        ids = self.list_ids()
        if not ids:
            return None
        return max(ids, key=lambda i: os.path.getmtime(self._file(i)))
