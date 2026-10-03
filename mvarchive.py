"""分卷归档顺序读取库（仅标准库）。

卷文件格式（固定 21 字节头 + 数据负载）::

    offset  size  字段
    0       4     magic = b"MVOL"
    4       1     格式版本 = 1
    5       4     卷号 volume_number，大端无符号，从 1 开始
    9       4     总卷数 total_volumes，大端无符号
    13      8     本卷负载长度 payload_length，大端无符号
    21      ...   负载数据

读取方按卷号（而非文件名字典序）排序拼接；缺卷、卷号重复、
total 不一致、头内声明长度与实际负载长度不一致都会显式报错，
绝不静默跳过。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator, List, Optional, Sequence, Tuple, Union

MAGIC = b"MVOL"
VERSION = 1
HEADER_SIZE = 21
_HEADER = struct.Struct(">4sBIIQ")

PathLike = Union[str, os.PathLike]

__all__ = [
    "MAGIC",
    "VERSION",
    "HEADER_SIZE",
    "VolumeInfo",
    "Gap",
    "MVArchiveError",
    "MissingVolumeError",
    "CorruptVolumeError",
    "write_volume",
    "read_header",
    "MultiVolumeReader",
]


class MVArchiveError(Exception):
    """分卷归档相关错误的基类。"""


class CorruptVolumeError(MVArchiveError):
    """卷头非法、元数据不一致或负载长度与声明不符。"""


@dataclass(frozen=True)
class Gap:
    """一段连续缺失的卷。

    first/last 为缺失卷号（闭区间）；byte_offset 是缺口在拼接后
    逻辑数据流中的起始字节偏移。
    """

    first: int
    last: int
    byte_offset: int

    def __str__(self) -> str:
        if self.first == self.last:
            return "卷 %d 缺失（逻辑偏移 %d）" % (self.first, self.byte_offset)
        return "卷 %d-%d 缺失（逻辑偏移 %d）" % (self.first, self.last, self.byte_offset)


class MissingVolumeError(MVArchiveError):
    """归档缺少一个或多个卷。gaps 精确定位每段缺口。"""

    def __init__(self, gaps: Sequence[Gap], total: int):
        self.gaps: Tuple[Gap, ...] = tuple(gaps)
        self.total = total
        missing = []
        for gap in self.gaps:
            missing.extend(range(gap.first, gap.last + 1))
        self.missing_volumes: Tuple[int, ...] = tuple(missing)
        detail = "；".join(str(g) for g in self.gaps)
        super().__init__("归档不完整：共应有 %d 卷，%s" % (total, detail))


@dataclass(frozen=True)
class VolumeInfo:
    """单个卷的元数据。"""

    volume_number: int
    total_volumes: int
    payload_length: int
    source: str


def _display_name(source: Union[PathLike, BinaryIO]) -> str:
    if isinstance(source, (str, bytes, os.PathLike)):
        return os.fspath(source)
    return getattr(source, "name", None) or "<file-like>"


def write_volume(source: Union[PathLike, BinaryIO], volume_number: int,
                 total_volumes: int, payload: bytes) -> int:
    """把 payload 写成一个卷文件，返回写入的总字节数（含头）。"""
    if not 1 <= volume_number <= 0xFFFFFFFF:
        raise ValueError("volume_number 必须在 1..2^32-1 之间")
    if not 1 <= total_volumes <= 0xFFFFFFFF:
        raise ValueError("total_volumes 必须在 1..2^32-1 之间")
    if volume_number > total_volumes:
        raise ValueError("volume_number 不能大于 total_volumes")
    header = _HEADER.pack(MAGIC, VERSION, volume_number, total_volumes, len(payload))
    data = header + bytes(payload)
    if isinstance(source, (str, bytes, os.PathLike)):
        with open(source, "wb") as fh:
            fh.write(data)
    else:
        source.write(data)
    return len(data)


def read_header(source: Union[PathLike, BinaryIO]) -> VolumeInfo:
    """读取并校验卷头；同时核对声明长度与实际负载长度。"""
    name = _display_name(source)
    if isinstance(source, (str, bytes, os.PathLike)):
        with open(source, "rb") as fh:
            raw = fh.read()
    else:
        raw = source.read()
    if len(raw) < HEADER_SIZE:
        raise CorruptVolumeError("%s: 文件太短（%d 字节），不是合法卷" % (name, len(raw)))
    magic, version, number, total, payload_length = _HEADER.unpack(raw[:HEADER_SIZE])
    if magic != MAGIC:
        raise CorruptVolumeError("%s: magic 不匹配，不是 MVOL 卷" % name)
    if version != VERSION:
        raise CorruptVolumeError("%s: 不支持的格式版本 %d" % (name, version))
    if number < 1:
        raise CorruptVolumeError("%s: 卷号 %d 非法（必须从 1 开始）" % (name, number))
    if total < 1:
        raise CorruptVolumeError("%s: 总卷数 %d 非法" % (name, total))
    if number > total:
        raise CorruptVolumeError("%s: 卷号 %d 超出总卷数 %d" % (name, number, total))
    actual = len(raw) - HEADER_SIZE
    if actual != payload_length:
        raise CorruptVolumeError(
            "%s: 卷内长度不一致，头声明 %d 字节，实际 %d 字节"
            % (name, payload_length, actual))
    return VolumeInfo(number, total, payload_length, name)


class MultiVolumeReader:
    """把一组卷文件按卷号拼接成一个可顺序读取的逻辑数据流。

    构造时即完成全部校验（缺卷、重复、total 不一致、长度不符），
    校验失败立即抛异常；校验通过后卷文件按需惰性打开。
    """

    def __init__(self, sources: Sequence[PathLike]):
        if not sources:
            raise MVArchiveError("至少需要一个卷文件")
        infos = [read_header(src) for src in sources]

        totals = {info.total_volumes for info in infos}
        if len(totals) != 1:
            raise CorruptVolumeError("各卷声明的总卷数不一致: %s" % sorted(totals))
        total = totals.pop()

        numbers = [info.volume_number for info in infos]
        if len(set(numbers)) != len(numbers):
            dup = sorted({n for n in numbers if numbers.count(n) > 1})
            raise CorruptVolumeError("卷号重复: %s" % dup)

        self._infos = sorted(infos, key=lambda info: info.volume_number)

        present = {info.volume_number for info in self._infos}
        missing = [n for n in range(1, total + 1) if n not in present]
        if missing:
            raise MissingVolumeError(self._locate_gaps(missing), total)

        self.total_volumes = total
        self.logical_size = sum(info.payload_length for info in self._infos)

        by_name = {os.fspath(src): src for src in sources}
        self._ordered_sources = [by_name[info.source] for info in self._infos]

        self._vol_index = 0
        self._fh: Optional[BinaryIO] = None
        self._pos = 0
        self._closed = False

    def _locate_gaps(self, missing: List[int]) -> List[Gap]:
        """把缺失卷号分成连续段，并计算每段在逻辑流中的字节偏移。"""
        present = {info.volume_number: info.payload_length for info in self._infos}
        gaps: List[Gap] = []
        start = prev = missing[0]
        for n in missing[1:] + [None]:  # type: ignore[list-item]
            if n is not None and n == prev + 1:
                prev = n
                continue
            offset = sum(length for num, length in present.items() if num < start)
            gaps.append(Gap(start, prev, offset))
            if n is not None:
                start = prev = n
        return gaps

    def _open_current(self) -> BinaryIO:
        if self._fh is None:
            self._fh = open(self._ordered_sources[self._vol_index], "rb")
            self._fh.seek(HEADER_SIZE)
        return self._fh

    def _advance(self) -> bool:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._vol_index += 1
        return self._vol_index < len(self._infos)

    @property
    def volumes(self) -> Tuple[VolumeInfo, ...]:
        """按逻辑顺序（卷号升序）排列的卷元数据。"""
        return tuple(self._infos)

    def tell(self) -> int:
        """当前在逻辑数据流中的字节偏移。"""
        return self._pos

    def read(self, size: int = -1) -> bytes:
        """顺序读取至多 size 字节；size<0 表示读到流尾。EOF 返回空字节串。"""
        if self._closed:
            raise ValueError("reader 已关闭")
        if size is None or size < 0:
            return self._read_all()
        if size == 0:
            return b""
        out = bytearray()
        while len(out) < size and self._vol_index < len(self._infos):
            fh = self._open_current()
            chunk = fh.read(size - len(out))
            if not chunk:
                if not self._advance():
                    break
                continue
            out += chunk
            self._pos += len(chunk)
        return bytes(out)

    def _read_all(self) -> bytes:
        out = bytearray()
        while self._vol_index < len(self._infos):
            fh = self._open_current()
            chunk = fh.read(1 << 20)
            if not chunk:
                if not self._advance():
                    break
                continue
            out += chunk
            self._pos += len(chunk)
        return bytes(out)

    def iter_chunks(self, size: int) -> Iterator[bytes]:
        """按固定块大小迭代，最后一块可能较短。"""
        if size <= 0:
            raise ValueError("块大小必须为正数")
        while True:
            chunk = self.read(size)
            if not chunk:
                return
            yield chunk

    def read_window(self, offset: int, length: int) -> bytes:
        """顺序流上读取 [offset, offset+length) 窗口（内部跳过前导字节）。"""
        if offset < 0 or length < 0:
            raise ValueError("offset/length 不能为负")
        remaining = offset
        while remaining > 0:
            skipped = self.read(min(remaining, 1 << 20))
            if not skipped:
                return b""
            remaining -= len(skipped)
        return self.read(length)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._closed = True

    def __enter__(self) -> "MultiVolumeReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
