"""dictz: 面向短消息的 LZSS 压缩器，支持从样本训练并持久化字典。

仅使用 Python 标准库，不依赖任何内置压缩模块（zlib/lzma/bz2 等）。

设计要点：
- 压缩格式带版本头（当前 v2）。v1 为无字典的旧格式，解码端保持兼容。
- 字典是从样本中训练得到的一段原始字节，压缩时作为 LZ 窗口的“前缀历史”，
  不写入压缩帧；帧内只记录 8 字节的 dict_id（字典内容的 SHA-256 前 8 字节）。
- 解压时若帧声明了 dict_id，则必须提供 id 匹配的字典，否则抛出
  DictMismatchError。旧字典文件可照常加载，配合 DictStore 可按帧内
  dict_id 自动选用对应字典，从而兼容读取历史数据。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

FRAME_MAGIC = b"DZ"
FORMAT_VERSION_LEGACY = 1      # 旧格式：无字典支持
FORMAT_VERSION_CURRENT = 2     # 当前格式：帧头可携带 dict_id
FLAG_HAS_DICT = 0x01

DICT_MAGIC = b"DZD1"
DICT_ID_LEN = 8

MIN_MATCH = 4
MAX_MATCH = MIN_MATCH + 255    # 长度字段 1 字节
WINDOW_SIZE = 65535            # 偏移字段 2 字节
HASH_LEN = 4                   # 匹配查找的哈希前缀长度
MAX_CHAIN = 32                 # 每个哈希桶最多回溯的候选数


class DictzError(Exception):
    """压缩/解压通用错误。"""


class DictMismatchError(DictzError):
    """解压时提供的字典与压缩帧声明的 dict_id 不一致。"""

    def __init__(self, needed, provided):
        self.needed = needed
        self.provided = provided
        super().__init__(
            "dictionary mismatch: frame needs dict_id=%s, provided=%s"
            % (needed, provided)
        )


# ---------------------------------------------------------------------------
# 字典
# ---------------------------------------------------------------------------

def dict_id(dictionary):
    """字典的稳定标识：内容 SHA-256 的前 8 字节。"""
    return hashlib.sha256(dictionary).digest()[:DICT_ID_LEN]


def _overlap_tail(dictionary, gram):
    """返回 dictionary 后缀与 gram 前缀的最长重叠长度。"""
    max_ov = min(len(dictionary), len(gram))
    for ov in range(max_ov, 0, -1):
        if dictionary[-ov:] == gram[:ov]:
            return ov
    return 0


def train_dictionary(samples, dict_size=4096, k=8):
    """从样本训练字典（简化版 COVER 算法）。

    1. 统计所有样本中长度为 k 的子串出现次数；
    2. 按频次降序贪心选取，尽量与已有字典后缀重叠合并；
    3. 若样本中没有任何重复子串（例如样本极少），退化为直接截取样本
       拼接内容——字典只是匹配参考，不进入压缩帧，不会损害压缩率。
    """
    samples = [bytes(s) for s in samples if s]
    if not samples or dict_size <= 0:
        return b""

    counts = {}
    for s in samples:
        for i in range(len(s) - k + 1):
            gram = s[i:i + k]
            counts[gram] = counts.get(gram, 0) + 1

    # 频次降序、字节序升序，保证结果确定性
    candidates = sorted(
        ((c, g) for g, c in counts.items() if c >= 2),
        key=lambda t: (-t[0], t[1]),
    )

    dictionary = bytearray()
    for _, gram in candidates:
        if len(dictionary) >= dict_size:
            break
        if bytes(gram) in bytes(dictionary):
            continue
        ov = _overlap_tail(dictionary, gram)
        space = dict_size - len(dictionary)
        dictionary += gram[ov:ov + space]

    if not dictionary:
        # 样本极少 / 无重复子串：退化为样本原文前缀
        blob = b"".join(samples)
        dictionary = bytearray(blob[:dict_size])

    return bytes(dictionary)


def save_dictionary(dictionary, path):
    """持久化字典，返回 dict_id。文件格式带魔数，便于未来演进。"""
    did = dict_id(dictionary)
    header = DICT_MAGIC + did + struct.pack(">I", len(dictionary))
    with open(path, "wb") as f:
        f.write(header + dictionary)
    return did


def load_dictionary(path):
    """加载字典文件并校验完整性。"""
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < 16 or blob[:4] != DICT_MAGIC:
        raise DictzError("not a dictz dictionary file: %s" % path)
    did = blob[4:12]
    (size,) = struct.unpack(">I", blob[12:16])
    content = blob[16:]
    if len(content) != size:
        raise DictzError("corrupt dictionary file: size mismatch")
    if dict_id(content) != did:
        raise DictzError("corrupt dictionary file: id mismatch")
    return content


class DictStore:
    """按 dict_id 管理多个持久化字典，用于兼容读取历史数据。

    字典训练算法升级或样本变化会产生新的 dict_id；只要旧字典文件仍
    保存在 store 目录中，旧数据即可照常解压。
    """

    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, did):
        return os.path.join(self.directory, "dict_%s.dzd" % did.hex())

    def save(self, dictionary):
        did = dict_id(dictionary)
        path = self._path(did)
        if not os.path.exists(path):
            save_dictionary(dictionary, path)
        return did

    def load(self, did):
        path = self._path(did)
        if not os.path.exists(path):
            raise DictMismatchError(needed=did.hex(), provided=None)
        return load_dictionary(path)

    def decompress(self, blob):
        """根据帧头中的 dict_id 自动选用字典解压。"""
        _, _, _, did, _ = _parse_header(blob)
        dictionary = self.load(did) if did is not None else b""
        return decompress(blob, dictionary)


# ---------------------------------------------------------------------------
# 压缩 / 解压
# ---------------------------------------------------------------------------

def _hash4(buf, pos):
    return int.from_bytes(buf[pos:pos + HASH_LEN], "big")


def _tokenize(data, dictionary):
    """LZSS 匹配：history = dictionary + data，返回 token 列表。

    token: (0, literal_byte) 或 (1, offset, length)
    """
    history = dictionary + data
    base = len(dictionary)
    n = len(data)
    table = {}

    def insert(p):
        if p + HASH_LEN > len(history):
            return
        key = _hash4(history, p)
        bucket = table.setdefault(key, [])
        bucket.append(p)
        if len(bucket) > MAX_CHAIN:
            del bucket[0]

    # 字典内容预先进入哈希表，作为匹配历史
    win_start = max(0, base - WINDOW_SIZE)
    for p in range(win_start, base):
        insert(p)

    tokens = []
    i = 0
    while i < n:
        gp = base + i
        best_len = 0
        best_off = 0
        if gp + HASH_LEN <= len(history):
            bucket = table.get(_hash4(history, gp))
            if bucket:
                max_len = min(MAX_MATCH, n - i)
                for p in reversed(bucket):
                    off = gp - p
                    if off > WINDOW_SIZE:
                        break
                    length = 0
                    while length < max_len and history[p + length] == history[gp + length]:
                        length += 1
                    if length > best_len:
                        best_len = length
                        best_off = off
                        if length >= max_len:
                            break
        if best_len >= MIN_MATCH:
            tokens.append((1, best_off, best_len))
            for j in range(gp, gp + best_len):
                insert(j)
            i += best_len
        else:
            tokens.append((0, data[i]))
            insert(gp)
            i += 1
    return tokens


def compress(data, dictionary=b"", _format_version=FORMAT_VERSION_CURRENT):
    """压缩 data。dictionary 非空时在帧头记录其 dict_id。"""
    data = bytes(data)
    dictionary = bytes(dictionary)
    tokens = _tokenize(data, dictionary)

    flags = FLAG_HAS_DICT if dictionary else 0
    out = bytearray()
    out += FRAME_MAGIC
    out.append(_format_version)
    out.append(flags)
    if _format_version >= 2 and flags & FLAG_HAS_DICT:
        out += dict_id(dictionary)

    for gstart in range(0, len(tokens), 8):
        group = tokens[gstart:gstart + 8]
        flag = 0
        body = bytearray()
        for bit, tok in enumerate(group):
            if tok[0] == 1:
                flag |= 0x80 >> bit
                _, off, length = tok
                body += off.to_bytes(2, "big")
                body.append(length - MIN_MATCH)
            else:
                body.append(tok[1])
        out.append(flag)
        out += body
    return bytes(out)


def _parse_header(blob):
    """返回 (version, flags, header_size, dict_id_or_None, blob)。"""
    if len(blob) < 4 or blob[:2] != FRAME_MAGIC:
        raise DictzError("not a dictz stream")
    version = blob[2]
    flags = blob[3]
    if version not in (FORMAT_VERSION_LEGACY, FORMAT_VERSION_CURRENT):
        raise DictzError("unsupported format version: %d" % version)
    pos = 4
    did = None
    if flags & FLAG_HAS_DICT:
        if version < 2:
            raise DictzError("corrupt stream: v1 frame cannot carry a dict_id")
        if len(blob) < pos + DICT_ID_LEN:
            raise DictzError("truncated stream header")
        did = blob[pos:pos + DICT_ID_LEN]
        pos += DICT_ID_LEN
    return version, flags, pos, did, blob


def decompress(blob, dictionary=b""):
    """解压。若帧声明了 dict_id，则 dictionary 必须与之匹配。"""
    blob = bytes(blob)
    dictionary = bytes(dictionary)
    _, _, pos, did, _ = _parse_header(blob)
    if did is not None:
        provided = dict_id(dictionary) if dictionary else None
        if provided != did:
            raise DictMismatchError(
                needed=did.hex(),
                provided=provided.hex() if provided else None,
            )

    out = bytearray(dictionary)
    while pos < len(blob):
        flag = blob[pos]
        pos += 1
        for bit in range(8):
            if pos >= len(blob):
                break
            if flag & (0x80 >> bit):
                if pos + 3 > len(blob):
                    raise DictzError("truncated stream")
                off = int.from_bytes(blob[pos:pos + 2], "big")
                length = blob[pos + 2] + MIN_MATCH
                pos += 3
                start = len(out) - off
                if start < 0:
                    raise DictzError("corrupt stream: offset out of range")
                for j in range(length):
                    out.append(out[start + j])
            else:
                out.append(blob[pos])
                pos += 1
    return bytes(out[len(dictionary):])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_files(paths):
    blobs = []
    for p in paths:
        with open(p, "rb") as f:
            blobs.append(f.read())
    return blobs


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="dictz", description="短消息压缩器（支持字典训练）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train", help="从样本文件训练字典")
    p_train.add_argument("samples", nargs="+", help="样本文件")
    p_train.add_argument("-o", "--output", required=True, help="字典输出路径")
    p_train.add_argument("--size", type=int, default=4096, help="字典大小（字节）")

    p_comp = sub.add_parser("compress", help="压缩文件")
    p_comp.add_argument("input")
    p_comp.add_argument("output")
    p_comp.add_argument("-d", "--dict", help="字典文件（可选）")

    p_deco = sub.add_parser("decompress", help="解压文件")
    p_deco.add_argument("input")
    p_deco.add_argument("output")
    p_deco.add_argument("-d", "--dict", help="字典文件（帧含 dict_id 时必需）")

    args = parser.parse_args(argv)

    if args.cmd == "train":
        dictionary = train_dictionary(_read_files(args.samples), args.size)
        did = save_dictionary(dictionary, args.output)
        print("dict_id=%s size=%d -> %s" % (did.hex(), len(dictionary), args.output))
    elif args.cmd == "compress":
        dictionary = load_dictionary(args.dict) if args.dict else b""
        with open(args.input, "rb") as f:
            data = f.read()
        with open(args.output, "wb") as f:
            f.write(compress(data, dictionary))
    elif args.cmd == "decompress":
        dictionary = load_dictionary(args.dict) if args.dict else b""
        with open(args.input, "rb") as f:
            blob = f.read()
        with open(args.output, "wb") as f:
            f.write(decompress(blob, dictionary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

