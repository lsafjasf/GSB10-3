"""diffseg - 差异段编码与恢复库（仅标准库）。

核心概念：
- Segment: 差异段，携带基准版本、偏移、长度、内容摘要（SHA-256）与内容本体。
- Backup:  一次备份 = 基准版本 -> 目标版本 的一组差异段 + 目标长度/整体摘要。
           同一份备份内段与段不得互相覆盖（validate 强制检查）。
- restore: 基准字节 + 按时间顺序排列的备份链 -> 目标字节。
           段重叠按“最新优先”处理，被覆盖的段记录进 RestoreReport。
           空洞（既不被基准也不被任何段覆盖的区域）显式报错，绝不填零。
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
from dataclasses import dataclass, field

FORMAT = "diffseg/1"


# ---------------------------------------------------------------- 异常

class DiffSegError(Exception):
    """库内所有异常的基类。"""


class SegmentOverlapError(DiffSegError):
    """同一份备份内段与段互相覆盖。"""


class DigestMismatchError(DiffSegError):
    """段或整体内容摘要校验失败。"""


class MissingBaseError(DiffSegError):
    """基准版本缺失，且差异段未能全覆盖目标文件。"""


class ChainError(DiffSegError):
    """备份链版本不连续。"""


class HoleError(DiffSegError):
    """恢复结果存在空洞（显式标记区间，绝不填零）。"""

    def __init__(self, holes, target_length):
        self.holes = list(holes)
        self.target_length = target_length
        pretty = ", ".join("[%d, %d)" % (s, e) for s, e in self.holes)
        super().__init__(
            "恢复结果存在 %d 处空洞（目标长度 %d）：%s"
            % (len(self.holes), target_length, pretty)
        )


def sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 差异段

@dataclass
class Segment:
    """差异段：基准版本 + 偏移 + 长度 + 内容摘要 + 内容本体。"""

    base_version: str
    offset: int
    length: int
    digest: str
    data: bytes

    @classmethod
    def from_data(cls, base_version: str, offset: int, data: bytes) -> "Segment":
        data = bytes(data)
        return cls(base_version, offset, len(data), sha256_hex(data), data)

    @property
    def end(self) -> int:
        return self.offset + self.length

    def verify(self) -> None:
        if self.length != len(self.data):
            raise DigestMismatchError(
                "段长度字段 %d 与实际内容 %d 不符" % (self.length, len(self.data))
            )
        actual = sha256_hex(self.data)
        if self.digest != actual:
            raise DigestMismatchError(
                "段摘要校验失败：声明 %s，实际 %s" % (self.digest, actual)
            )

    def to_dict(self) -> dict:
        return {
            "base_version": self.base_version,
            "offset": self.offset,
            "length": self.length,
            "digest": self.digest,
            "data_b64": base64.b64encode(self.data).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        seg = cls(
            base_version=d["base_version"],
            offset=int(d["offset"]),
            length=int(d["length"]),
            digest=d["digest"],
            data=base64.b64decode(d["data_b64"]),
        )
        seg.verify()
        return seg


# ---------------------------------------------------------------- 备份

@dataclass
class Backup:
    """一次备份：base_version -> target_version 的差异段集合。"""

    base_version: str
    target_version: str
    target_length: int
    target_digest: str
    segments: list = field(default_factory=list)

    def validate(self) -> None:
        """校验：段摘要、越界，以及同一份备份内段不得互相覆盖。"""
        if self.target_length < 0:
            raise DiffSegError("target_length 不能为负")
        prev = None
        for seg in sorted(self.segments, key=lambda s: (s.offset, s.length)):
            seg.verify()
            if seg.base_version != self.base_version:
                raise DiffSegError(
                    "段声明的基准版本 %r 与备份基准版本 %r 不一致"
                    % (seg.base_version, self.base_version)
                )
            if seg.offset < 0 or seg.length <= 0:
                raise DiffSegError("段偏移/长度非法: offset=%d length=%d"
                                   % (seg.offset, seg.length))
            if seg.end > self.target_length:
                raise DiffSegError(
                    "段 [%d, %d) 超出目标长度 %d" % (seg.offset, seg.end, self.target_length)
                )
            if prev is not None and seg.offset < prev.end:
                raise SegmentOverlapError(
                    "同一份备份 %r 内段互相覆盖: [%d, %d) 与 [%d, %d)"
                    % (self.target_version, prev.offset, prev.end, seg.offset, seg.end)
                )
            prev = seg

    def to_json(self) -> str:
        return json.dumps(
            {
                "format": FORMAT,
                "base_version": self.base_version,
                "target_version": self.target_version,
                "target_length": self.target_length,
                "target_digest": self.target_digest,
                "segments": [s.to_dict() for s in self.segments],
            },
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "Backup":
        d = json.loads(text)
        if d.get("format") != FORMAT:
            raise DiffSegError("未知格式: %r" % d.get("format"))
        bk = cls(
            base_version=d["base_version"],
            target_version=d["target_version"],
            target_length=int(d["target_length"]),
            target_digest=d["target_digest"],
            segments=[Segment.from_dict(x) for x in d["segments"]],
        )
        bk.validate()
        return bk


# ---------------------------------------------------------------- 编码

def compute_diff(base: bytes, target: bytes, base_version: str, target_version: str) -> Backup:
    """计算 base -> target 的差异段备份。

    与基准同偏移、同内容的区域由基准覆盖，不产生段；
    其余目标区域（含插入/删除导致的位移区）生成为绝对偏移的写段。
    """
    base = bytes(base)
    target = bytes(target)
    matcher = difflib.SequenceMatcher(a=base, b=target, autojunk=False)
    segments = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal" and a0 == b0:
            continue  # 基准同偏移覆盖，无需段
        chunk = target[b0:b1]
        if chunk:
            segments.append(Segment.from_data(base_version, b0, chunk))
    segments = _merge_adjacent(segments)
    bk = Backup(base_version, target_version, len(target), sha256_hex(target), segments)
    bk.validate()
    return bk


def _merge_adjacent(segments):
    merged = []
    for seg in sorted(segments, key=lambda s: s.offset):
        if merged and merged[-1].end == seg.offset:
            prev = merged[-1]
            merged[-1] = Segment.from_data(prev.base_version, prev.offset, prev.data + seg.data)
        else:
            merged.append(seg)
    return merged


# ---------------------------------------------------------------- 恢复

@dataclass
class OverwriteRecord:
    """一次覆盖事件：covered_label 的 [s, e) 区间被 by_label 覆盖。"""

    covered_label: str
    covered_kind: str  # "base" 或 "segment"
    covered_range: tuple
    by_label: str

    def to_dict(self) -> dict:
        return {
            "covered": self.covered_label,
            "covered_kind": self.covered_kind,
            "range": list(self.covered_range),
            "by": self.by_label,
        }


@dataclass
class RestoreReport:
    base_version: str
    target_version: str
    target_length: int
    applied_segments: list
    overwritten: list  # list[OverwriteRecord]，仅含段被覆盖的记录

    def to_dict(self) -> dict:
        return {
            "base_version": self.base_version,
            "target_version": self.target_version,
            "target_length": self.target_length,
            "applied_segments": list(self.applied_segments),
            "overwritten": [r.to_dict() for r in self.overwritten],
        }


# 覆盖画布区间: (start, end, kind, ref, src_off, label)
#   kind="base" -> 数据来自 base[src_off:...]
#   kind="seg"  -> 数据来自 backups[ref[0]].segments[ref[1]].data[src_off:...]

def _slice_interval(iv, a, b):
    start, _end, kind, ref, src_off, label = iv
    return (a, b, kind, ref, src_off + (a - start), label)


def _apply_interval(intervals, new_iv, overwritten):
    """把新区间按“最新优先”压入画布，记录被覆盖的旧区间。"""
    start, end = new_iv[0], new_iv[1]
    new_label = new_iv[5]
    out = []
    for iv in intervals:
        a, b = iv[0], iv[1]
        if b <= start or a >= end:
            out.append(iv)
            continue
        ov_s, ov_e = max(a, start), min(b, end)
        overwritten.append(
            OverwriteRecord(
                covered_label=iv[5],
                covered_kind=iv[2],
                covered_range=(ov_s, ov_e),
                by_label=new_label,
            )
        )
        if a < ov_s:
            out.append(_slice_interval(iv, a, ov_s))
        if ov_e < b:
            out.append(_slice_interval(iv, ov_e, b))
    out.append(new_iv)
    out.sort(key=lambda x: x[0])
    return out


def _find_holes(intervals, target_length):
    holes = []
    cursor = 0
    for iv in sorted(intervals, key=lambda x: x[0]):
        if iv[0] > cursor:
            holes.append((cursor, iv[0]))
        cursor = max(cursor, iv[1])
    if cursor < target_length:
        holes.append((cursor, target_length))
    return holes


def restore(base, backups, base_version=None):
    """按备份链恢复目标文件。

    参数:
        base: 基准版本字节；None 表示基准缺失。
        backups: 按时间顺序（旧 -> 新）排列的 Backup 列表。
        base_version: 基准版本名，缺省取第一个备份的 base_version。

    返回 (target_bytes, RestoreReport)。
    空洞显式报错（HoleError / MissingBaseError），绝不填零。
    """
    backups = list(backups)
    if not backups:
        raise DiffSegError("空备份链")
    if base_version is None:
        base_version = backups[0].base_version

    expected = base_version
    for bk in backups:
        bk.validate()
        if bk.base_version != expected:
            raise ChainError(
                "备份链断裂：期望基准版本 %r，实际 %r" % (expected, bk.base_version)
            )
        expected = bk.target_version

    target = backups[-1]
    target_length = target.target_length

    intervals = []
    if base is not None:
        hi = min(len(base), target_length)
        if hi > 0:
            intervals.append((0, hi, "base", None, 0, "base:%s" % base_version))

    overwritten_all = []
    applied = []
    for bi, bk in enumerate(backups):
        ordered = sorted(enumerate(bk.segments), key=lambda p: p[1].offset)
        for si, seg in ordered:
            seg.verify()
            if seg.offset >= target_length:
                continue  # 段整体超出最终目标长度（如后续版本又截断），丢弃
            label = "%s#seg%d@%d+%d" % (bk.target_version, si, seg.offset, seg.length)
            end = min(seg.end, target_length)
            intervals = _apply_interval(
                intervals,
                (seg.offset, end, "seg", (bi, si), 0, label),
                overwritten_all,
            )
            applied.append(label)

    holes = _find_holes(intervals, target_length)
    if holes:
        pretty = ", ".join("[%d, %d)" % (s, e) for s, e in holes)
        if base is None:
            raise MissingBaseError(
                "基准版本 %r 缺失，且差异段未全覆盖目标文件；空洞: %s"
                % (base_version, pretty)
            )
        raise HoleError(holes, target_length)

    out = bytearray(target_length)
    for (a, b, kind, ref, src_off, _label) in intervals:
        n = b - a
        if kind == "base":
            out[a:b] = base[src_off:src_off + n]
        else:
            bi, si = ref
            out[a:b] = backups[bi].segments[si].data[src_off:src_off + n]
    result = bytes(out)

    actual = sha256_hex(result)
    if actual != target.target_digest:
        raise DigestMismatchError(
            "恢复结果整体摘要校验失败：期望 %s，实际 %s" % (target.target_digest, actual)
        )

    report = RestoreReport(
        base_version=base_version,
        target_version=target.target_version,
        target_length=target_length,
        applied_segments=applied,
        overwritten=[r for r in overwritten_all if r.covered_kind == "seg"],
    )
    return result, report
