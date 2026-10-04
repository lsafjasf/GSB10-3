"""多副本页存储：页级 CRC 校验、损坏定位与多数表决修复（仅标准库）。

页布局（小端）::

    | magic(4) | page_no(4) | version(8) | data_len(4) | crc32(4) | data ... |

crc32 覆盖 magic + page_no + version + data_len + data，即除 crc 字段外的
全部页字节。

副本文件是追加式日志，每条记录为 ``<u32 长度><页映像>``。修复只追加新版本
页，绝不原地修改旧页字节，修复前的现场始终保留在磁盘上，旧版本可回溯。
"""
from __future__ import annotations

import os
import struct
import zlib
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

MAGIC = b"PG01"
HEADER = struct.Struct("<4sIQII")  # magic, page_no, version, data_len, crc32
HEADER_SIZE = HEADER.size
MAGIC_OFFSET = 0
PAGE_NO_OFFSET = 4
VERSION_OFFSET = 8
DATA_LEN_OFFSET = 16
CRC_OFFSET = 20


def compute_crc(page_no: int, version: int, data: bytes) -> int:
    """计算页校验值：覆盖除 crc 字段外的全部页字节（含页头元数据）。"""
    crc = zlib.crc32(MAGIC)
    crc = zlib.crc32(struct.pack("<IQI", page_no, version, len(data)), crc)
    return zlib.crc32(bytes(data), crc) & 0xFFFFFFFF


def encode_page(page_no: int, version: int, data: bytes) -> bytes:
    data = bytes(data)
    crc = compute_crc(page_no, version, data)
    return HEADER.pack(MAGIC, page_no, version, len(data), crc) + data


@dataclass
class PageImage:
    page_no: int
    version: int
    data: bytes
    stored_crc: int
    computed_crc: int
    raw: bytes
    header_ok: bool

    @property
    def crc_ok(self) -> bool:
        return self.header_ok and self.stored_crc == self.computed_crc


def decode_page(raw: bytes) -> PageImage:
    if len(raw) < HEADER_SIZE:
        return PageImage(-1, -1, b"", 0, 0, raw, False)
    magic, page_no, version, data_len, stored_crc = HEADER.unpack(raw[:HEADER_SIZE])
    header_ok = magic == MAGIC and len(raw) == HEADER_SIZE + data_len
    data = raw[HEADER_SIZE:HEADER_SIZE + data_len]
    computed = compute_crc(page_no, version, data)
    return PageImage(page_no, version, data, stored_crc, computed, raw, header_ok)


class ReplicaStore:
    """单副本：追加式日志文件，记录格式为 <u32 长度><页映像>。"""

    def __init__(self, path: str):
        self.path = path

    def append_page(self, page_no: int, version: int, data: bytes):
        """写一个新页（只追加，不覆盖旧页），返回 (version, crc)。"""
        data = bytes(data)
        image = encode_page(page_no, version, data)
        with open(self.path, "ab") as fh:
            fh.write(struct.pack("<I", len(image)))
            fh.write(image)
        return version, compute_crc(page_no, version, data)

    def _iter_records(self):
        if not os.path.exists(self.path):
            return
        offset = 0
        with open(self.path, "rb") as fh:
            while True:
                head = fh.read(4)
                if len(head) < 4:
                    return
                (length,) = struct.unpack("<I", head)
                raw = fh.read(length)
                if len(raw) < length:
                    return
                yield offset, raw
                offset += 4 + length

    def read_latest(self, page_no: int):
        """读取该页版本号最大的页映像；不存在返回 None。"""
        best = None
        for _offset, raw in self._iter_records():
            img = decode_page(raw)
            if img.page_no == page_no and (best is None or img.version >= best.version):
                best = img
        return best

    def tamper(self, page_no: int, page_offset: int, xor_mask: int = 0xFF) -> int:
        """测试/演示辅助：原地翻转指定页最新版本中的一个字节，返回文件偏移。"""
        target = None
        for rec_offset, raw in self._iter_records():
            img = decode_page(raw)
            if img.page_no == page_no and (target is None or img.version >= target[1]):
                target = (rec_offset, img.version)
        if target is None:
            raise KeyError(f"page {page_no} not found in {self.path}")
        abs_offset = target[0] + 4 + page_offset
        with open(self.path, "r+b") as fh:
            fh.seek(abs_offset)
            original = fh.read(1)
            if not original:
                raise ValueError("page_offset out of range")
            fh.seek(abs_offset)
            fh.write(bytes([original[0] ^ xor_mask]))
        return abs_offset


class ReplicaStatus(Enum):
    OK = "ok"                          # 校验自洽，且与多数一致
    STALE = "stale"                    # 校验自洽，但数据/版本落后于多数
    MISSING = "missing"                # 副本中没有该页
    HEADER_CORRUPT = "header_corrupt"  # 页头字段损坏（magic/data_len 等）
    DATA_CORRUPT = "data_corrupt"      # 校验失败，数据与多数参照不一致
    CRC_SUSPECT = "crc_suspect"        # 数据与多数一致，仅 crc 字段不自洽
    VERSION_SUSPECT = "version_suspect"  # 数据与多数一致，仅 version 字段不自洽
    UNVERIFIABLE = "unverifiable"      # 校验失败且无健康参照，无法定位


@dataclass
class Corruption:
    region: str   # header:magic / header:version / header:crc / header:data_len / data
    offset: int   # 页内绝对偏移
    length: int

    def __str__(self):
        return f"{self.region}@页内偏移 {self.offset} 起 {self.length} 字节"


@dataclass
class ReplicaDiag:
    replica: int
    status: ReplicaStatus
    version: int | None = None
    stored_crc: int | None = None
    computed_crc: int | None = None
    corruptions: list = field(default_factory=list)


@dataclass
class ReadReport:
    page_no: int
    replicas: list
    consensus: bytes | None  # 过半健康副本一致的数据；否则 None

    @property
    def healthy(self) -> bool:
        return self.consensus is not None


@dataclass
class ReplicaChange:
    replica: int
    action: str           # keep / repaired
    version_before: int | None
    version_after: int | None
    crc_before: int | None
    crc_after: int | None


class Decision(Enum):
    NO_REPAIR_NEEDED = "no_repair_needed"
    REPAIRED = "repaired"
    ABORT_NO_MAJORITY = "abort_no_majority"            # 含票数相同
    ABORT_NO_HEALTHY_SOURCE = "abort_no_healthy_source"


@dataclass
class RepairReport:
    page_no: int
    decision: Decision
    reason: str
    read_report: ReadReport
    changes: list


def _diff_ranges(actual: bytes, expected: bytes, base: int):
    """逐字节比对，返回实际数据中与参照不同的连续区间（绝对偏移加 base）。"""
    ranges = []
    start = None
    common = min(len(actual), len(expected))
    for i in range(common):
        if actual[i] != expected[i]:
            if start is None:
                start = i
        elif start is not None:
            ranges.append(Corruption("data", base + start, i - start))
            start = None
    if start is not None:
        ranges.append(Corruption("data", base + start, common - start))
    if len(actual) != len(expected):
        ranges.append(Corruption("data", base + common, abs(len(actual) - len(expected))))
    return ranges


class MultiReplicaStore:
    """多副本存储：读页校验定位 + 多数表决写新页修复。"""

    def __init__(self, replicas):
        self.replicas = list(replicas)

    def write_page(self, page_no: int, data: bytes) -> int:
        """向全部副本追加同一新版本，返回新版本号。"""
        version = 0
        for store in self.replicas:
            img = store.read_latest(page_no)
            if img is not None and img.version > version:
                version = img.version
        version += 1
        for store in self.replicas:
            store.append_page(page_no, version, data)
        return version

    def _diagnose(self, index: int, img, ref) -> ReplicaDiag:
        if img is None:
            return ReplicaDiag(index, ReplicaStatus.MISSING)
        diag = ReplicaDiag(index, ReplicaStatus.OK, img.version,
                          img.stored_crc, img.computed_crc)
        if not img.header_ok:
            diag.status = ReplicaStatus.HEADER_CORRUPT
            if len(img.raw) < HEADER_SIZE:
                diag.corruptions.append(Corruption("header", 0, len(img.raw) or 1))
                return diag
            if img.raw[:4] != MAGIC:
                diag.corruptions.append(Corruption("header:magic", MAGIC_OFFSET, 4))
            _magic, _pno, _ver, data_len, _crc = HEADER.unpack(img.raw[:HEADER_SIZE])
            if len(img.raw) != HEADER_SIZE + data_len:
                diag.corruptions.append(Corruption("header:data_len", DATA_LEN_OFFSET, 4))
            if not diag.corruptions:
                diag.corruptions.append(Corruption("header", 0, HEADER_SIZE))
            return diag
        if img.crc_ok:
            if ref is not None and img.data != ref.data:
                diag.status = ReplicaStatus.STALE
            return diag
        if ref is None:
            diag.status = ReplicaStatus.UNVERIFIABLE
            return diag
        if img.data == ref.data:
            if img.version != ref.version:
                diag.status = ReplicaStatus.VERSION_SUSPECT
                diag.corruptions.append(Corruption("header:version", VERSION_OFFSET, 8))
            else:
                diag.status = ReplicaStatus.CRC_SUSPECT
                diag.corruptions.append(Corruption("header:crc", CRC_OFFSET, 4))
        else:
            diag.status = ReplicaStatus.DATA_CORRUPT
            diag.corruptions.extend(_diff_ranges(img.data, ref.data, HEADER_SIZE))
        return diag

    def read_page(self, page_no: int) -> ReadReport:
        """读页：逐副本校验并定位损坏；consensus 为过半健康副本一致的数据。"""
        images = [store.read_latest(page_no) for store in self.replicas]
        valid = [img for img in images if img is not None and img.crc_ok]
        votes = Counter(img.data for img in valid)
        ref = None
        consensus = None
        if votes:
            top_data, top_votes = votes.most_common(1)[0]
            ref = next(img for img in valid if img.data == top_data)
            if top_votes > len(self.replicas) // 2:
                consensus = top_data
        diags = [self._diagnose(i, img, ref) for i, img in enumerate(images)]
        return ReadReport(page_no, diags, consensus)

    def repair_page(self, page_no: int) -> RepairReport:
        """按多数表决修复；票数相同或无过半健康副本时放弃，绝不写出。"""
        report = self.read_page(page_no)
        n = len(self.replicas)
        images = [store.read_latest(page_no) for store in self.replicas]
        valid = [img for img in images if img is not None and img.crc_ok]
        if not valid:
            return RepairReport(page_no, Decision.ABORT_NO_HEALTHY_SOURCE,
                                "全部副本校验失败，没有可信修复来源", report, [])
        votes = Counter(img.data for img in valid)
        ranking = votes.most_common()
        top_data, top_votes = ranking[0]
        tied = len(ranking) > 1 and ranking[1][1] == top_votes
        if tied:
            split = ", ".join(f"{k[:8].hex()}…={v}票" for k, v in ranking)
            return RepairReport(page_no, Decision.ABORT_NO_MAJORITY,
                                f"票数相同，无过半多数（{split}），放弃修复", report, [])
        if top_votes <= n // 2:
            return RepairReport(
                page_no, Decision.ABORT_NO_MAJORITY,
                f"健康副本不足过半：最高仅 {top_votes}/{n} 票"
                f"（多数副本可能损坏），拒绝用少数数据覆盖多数副本",
                report, [])
        new_version = max(img.version for img in images if img is not None) + 1
        changes = []
        for i, img in enumerate(images):
            if img is not None and img.crc_ok and img.data == top_data:
                changes.append(ReplicaChange(i, "keep", img.version, img.version,
                                             img.stored_crc, img.stored_crc))
                continue
            before_v = img.version if img is not None else None
            before_c = img.stored_crc if img is not None else None
            _v, new_crc = self.replicas[i].append_page(page_no, new_version, top_data)
            changes.append(ReplicaChange(i, "repaired", before_v, new_version,
                                         before_c, new_crc))
        if all(c.action == "keep" for c in changes):
            return RepairReport(page_no, Decision.NO_REPAIR_NEEDED,
                                "全部副本一致且校验通过", report, changes)
        return RepairReport(
            page_no, Decision.REPAIRED,
            f"多数 {top_votes}/{n} 票一致，以其为来源写新页（版本 {new_version}）",
            report, changes)
