"""分卷归档的顺序读取库（仅标准库）。

卷文件布局::

    +-------------------+----------------------+
    |      卷头 20B     |      数据载荷        |
    +-------------------+----------------------+

卷头（大端）::

    magic      4B  固定 b"MVAR"
    version    1B  当前为 1
    reserved   1B  保留，写 0
    vol_no     2B  卷号，从 1 开始
    vol_total  2B  总卷数
    data_len   8B  本卷数据载荷字节数
    crc32      4B  本卷数据载荷的 zlib.crc32

读取完全以卷头中的 ``vol_no`` / ``vol_total`` 为准，与文件名无关。
卷必须从 1 连续到 vol_total；一旦发现缺口或卷内长度与卷头不符，
立即抛出异常并定位缺口，绝不静默跳过。
"""

from __future__ import annotations

import dataclasses
import os
import struct
import zlib
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

MAGIC = b"MVAR"
VERSION = 1
HEADER = struct.Struct(">4sBBHHQI")  # 20 字节
HEADER_SIZE = HEADER.size

PathLike = Union[str, os.PathLike]


class MVAError(Exception):
    """本库所有错误的基类。"""


class BadVolumeHeaderError(MVAError):
    """卷头无法解析（magic/version 错误或文件被截断）。"""


class InconsistentVolumeError(MVAError):
    """卷之间或卷头与卷内容之间不一致。"""


@dataclasses.dataclass
class Gap:
    """一个缺失卷缺口。

    missing: 缺失的卷号列表（连续区间）
    after_vol / before_vol: 缺口两侧实际存在的卷号（端点缺失时为 None）
    byte_offset: 缺口起点在拼接后逻辑数据流中的字节偏移
    """

    missing: List[int]
    after_vol: Optional[int]
    before_vol: Optional[int]
    byte_offset: int

    def describe(self) -> str:
        nums = _format_ranges(self.missing)
        loc = f"逻辑偏移 0x{self.byte_offset:x}({self.byte_offset} 字节)"
        if self.after_vol is None and self.before_vol is None:
            return f"缺失卷 {nums}（{loc}）"
        if self.before_vol is None:
            return f"缺失卷 {nums}：位于卷 {self.after_vol} 之后、流尾之前（{loc}）"
        if self.after_vol is None:
            return f"缺失卷 {nums}：位于流首、卷 {self.before_vol} 之前（{loc}）"
        return (
            f"缺失卷 {nums}：位于卷 {self.after_vol} 与卷 {self.before_vol} 之间"
            f"（{loc}）"
        )


class MissingVolumeError(MVAError):
    """检测到一个或多个缺失卷缺口。"""

    def __init__(self, gaps: Sequence[Gap], vol_total: int):
        self.gaps: List[Gap] = list(gaps)
        self.vol_total = vol_total
        detail = "; ".join(g.describe() for g in self.gaps)
        super().__init__(f"分卷不完整（总卷数 {vol_total}）：{detail}")


@dataclasses.dataclass(frozen=True)
class VolumeMeta:
    vol_no: int
    vol_total: int
    data_len: int
    crc32: int
    path: Path


def _format_ranges(nums: Sequence[int]) -> str:
    if not nums:
        return ""
    parts: List[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def write_volumes(
    data: bytes,
    out_dir: PathLike,
    max_data_len: int,
    prefix: str = "part",
) -> List[Path]:
    """把 ``data`` 切成多卷写入 ``out_dir``，返回写出的路径列表。

    空数据也会产生 1 个载荷为空的卷。文件名只供人辨认，读取时不依赖它。
    """
    if max_data_len <= 0:
        raise ValueError("max_data_len 必须为正整数")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    total = max(1, (len(data) + max_data_len - 1) // max_data_len)
    paths: List[Path] = []
    for idx in range(total):
        vol_no = idx + 1
        chunk = data[idx * max_data_len : (idx + 1) * max_data_len]
        header = HEADER.pack(
            MAGIC, VERSION, 0, vol_no, total, len(chunk), zlib.crc32(chunk) & 0xFFFFFFFF
        )
        path = out / f"{prefix}.{vol_no:05d}-of-{total:05d}.bin"
        path.write_bytes(header + chunk)
        paths.append(path)
    return paths


def parse_volume(path: PathLike) -> VolumeMeta:
    """解析单个卷文件的卷头并校验文件长度，不读取载荷。"""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise MVAError(f"无法读取卷文件 {p}: {exc}") from exc
    if len(raw) < HEADER_SIZE:
        raise BadVolumeHeaderError(f"{p}: 文件长度 {len(raw)} 不足卷头 {HEADER_SIZE} 字节")
    magic, version, _reserved, vol_no, vol_total, data_len, crc = HEADER.unpack(
        raw[:HEADER_SIZE]
    )
    if magic != MAGIC:
        raise BadVolumeHeaderError(f"{p}: magic 错误，不是本格式的卷文件")
    if version != VERSION:
        raise BadVolumeHeaderError(f"{p}: 不支持的卷格式版本 {version}")
    actual = len(raw) - HEADER_SIZE
    if actual != data_len:
        raise InconsistentVolumeError(
            f"{p}: 卷头声明载荷 {data_len} 字节，但文件内实际载荷为 {actual} 字节"
            f"（声明 {'偏短' if actual > data_len else '偏长'}，疑似被追加或截断）"
        )
    if vol_total == 0 or not (1 <= vol_no <= vol_total):
        raise InconsistentVolumeError(
            f"{p}: 非法卷号/总数 vol_no={vol_no} vol_total={vol_total}"
        )
    return VolumeMeta(vol_no, vol_total, data_len, crc, p)


def discover_volumes(
    source: Union[PathLike, Iterable[PathLike]],
    *,
    pattern: str = "*.bin",
    verify_crc: bool = False,
) -> List[VolumeMeta]:
    """从目录或显式文件列表中发现并解析卷，按卷号升序返回。"""
    if isinstance(source, (str, os.PathLike)):
        base = Path(source)
        if base.is_dir():
            files = sorted(base.glob(pattern))
        else:
            files = [base]
    else:
        files = [Path(f) for f in source]
    if not files:
        raise MVAError("没有找到任何卷文件")

    metas = [parse_volume(f) for f in files]

    seen: dict = {}
    for m in metas:
        if m.vol_no in seen:
            raise InconsistentVolumeError(
                f"卷号 {m.vol_no} 重复：{seen[m.vol_no].path} 与 {m.path}"
            )
        seen[m.vol_no] = m

    totals = {m.vol_total for m in metas}
    if len(totals) > 1:
        raise InconsistentVolumeError(
            "各卷头中的总卷数不一致：" + ", ".join(sorted(str(t) for t in totals))
        )

    if verify_crc:
        for m in metas:
            payload = m.path.read_bytes()[HEADER_SIZE:]
            actual = zlib.crc32(payload) & 0xFFFFFFFF
            if actual != m.crc32:
                raise InconsistentVolumeError(
                    f"{m.path}: 卷 {m.vol_no} CRC32 校验失败"
                    f"（头中 {m.crc32:08x}，实际 {actual:08x}）"
                )

    return sorted(metas, key=lambda m: m.vol_no)


def find_gaps(metas: Sequence[VolumeMeta]) -> List[Gap]:
    """在已按卷号排序的元信息中定位全部缺口。"""
    if not metas:
        return []
    total = metas[0].vol_total
    present = {m.vol_no: m for m in metas}
    gaps: List[Gap] = []
    offset = 0  # 当前卷之前所有载荷的累计长度
    pending: List[int] = []

    def flush(after: Optional[VolumeMeta], before: Optional[VolumeMeta]) -> None:
        if pending:
            gaps.append(
                Gap(list(pending), after.vol_no if after else None,
                    before.vol_no if before else None, offset)
            )
            pending.clear()

    prev: Optional[VolumeMeta] = None
    for no in range(1, total + 1):
        if no in present:
            cur = present[no]
            flush(prev, cur)
            offset += cur.data_len
            prev = cur
        else:
            pending.append(no)
    flush(prev, None)
    return gaps


class VolumeReader:
    """按逻辑顺序读取完整数据流的只读文件对象。

    构造时即完成严格校验（含缺口检测），缺口会抛 MissingVolumeError，
    之后任何 read() 都不会静默跨过缺口。
    """

    def __init__(
        self,
        source: Union[PathLike, Iterable[PathLike]],
        *,
        pattern: str = "*.bin",
        verify_crc: bool = False,
    ):
        self._metas = discover_volumes(source, pattern=pattern, verify_crc=verify_crc)
        self._total = self._metas[0].vol_total
        gaps = find_gaps(self._metas)
        if gaps:
            raise MissingVolumeError(gaps, self._total)

        self._index = 0
        self._fh = None
        self._pos = 0
        self._length = sum(m.data_len for m in self._metas)
        self._closed = False

    @property
    def total_volumes(self) -> int:
        return self._total

    @property
    def length(self) -> int:
        """拼接后完整数据流的字节数。"""
        return self._length

    @property
    def volumes(self) -> List[VolumeMeta]:
        return list(self._metas)

    def __enter__(self) -> "VolumeReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __len__(self) -> int:
        return self._length

    def _open_current(self) -> None:
        if self._fh is None:
            m = self._metas[self._index]
            self._fh = open(m.path, "rb")
            self._fh.seek(HEADER_SIZE)

    def read(self, size: int = -1) -> bytes:
        """读取至多 ``size`` 字节；``size`` 为负或省略时读到流尾。

        自动透明跨越卷边界；跨卷拼接结果与整卷读取完全一致。
        """
        if self._closed:
            raise ValueError("reader 已关闭")
        if size == 0 or self._index >= len(self._metas):
            return b""
        if size is None or size < 0:
            parts = []
            while self._index < len(self._metas):
                self._open_current()
                parts.append(self._fh.read())
                self._fh.close()
                self._fh = None
                self._index += 1
            out = b"".join(parts)
            self._pos += len(out)
            return out

        remaining = size
        parts = []
        while remaining > 0 and self._index < len(self._metas):
            m = self._metas[self._index]
            self._open_current()
            chunk = self._fh.read(remaining)
            parts.append(chunk)
            remaining -= len(chunk)
            self._pos += len(chunk)
            if self._fh.tell() == HEADER_SIZE + m.data_len:
                self._fh.close()
                self._fh = None
                self._index += 1
        return b"".join(parts)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if self._closed:
            raise ValueError("reader 已关闭")
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self._pos + offset
        elif whence == os.SEEK_END:
            target = self._length + offset
        else:
            raise ValueError(f"非法 whence: {whence}")
        if target < 0:
            raise ValueError("seek 位置不能为负")
        target = min(target, self._length)

        if self._fh is not None:
            self._fh.close()
            self._fh = None

        acc = 0
        for i, m in enumerate(self._metas):
            if target <= acc + m.data_len:
                self._index = i
                self._pos = target
                if target < self._length:
                    self._open_current()
                    self._fh.seek(HEADER_SIZE + (target - acc))
                else:
                    self._index = len(self._metas)
                return target
            acc += m.data_len
        self._index = len(self._metas)
        self._pos = target
        return target

    def tell(self) -> int:
        return self._pos

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._closed = True

    def read_all_concat(self) -> bytes:
        """一次性读出完整数据流（对拍基准：整卷读取后拼接）。"""
        return b"".join(
            m.path.read_bytes()[HEADER_SIZE:] for m in self._metas
        )


def open_volume_stream(
    source: Union[PathLike, Iterable[PathLike]],
    *,
    pattern: str = "*.bin",
    verify_crc: bool = False,
) -> VolumeReader:
    """便捷入口：打开一个跨卷顺序读取流。"""
    return VolumeReader(source, pattern=pattern, verify_crc=verify_crc)
