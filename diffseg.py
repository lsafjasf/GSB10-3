"""差异段编码与恢复库（仅使用 Python 标准库）。

模型：
- 一个全量基准版本（base）加上一串差异备份（Backup）构成备份链。
- 每个 Backup 记录：自身版本号、基准版本号、目标文件长度、目标整体摘要，
  以及一组互不重叠的差异段（Segment）。
- 每个 Segment 记录：基准版本、偏移、长度、内容摘要（SHA-256）与内容本体。

恢复规则：
- 备份链按从新到旧的顺序应用差异段，新段优先（latest-wins）；
  被新段覆盖的旧段（或其被覆盖部分）会记录进恢复报告。
- 段未覆盖到的区域是“空洞”。空洞只能由基准版本内容填充，
  且必须显式记录在报告中；基准缺失或基准不够长时抛出 MissingBaseError，
  绝不用零字节静默填充。
"""

from __future__ import annotations

import base64
import bisect
import hashlib
import json
from dataclasses import dataclass, field

FORMAT = "diffseg/1"
DEFAULT_BLOCK_SIZE = 4096


class DiffSegError(Exception):
    """库内所有异常的基类。"""


class ValidationError(DiffSegError):
    """备份数据本身不合法（同包段重叠、长度/摘要不符等）。"""


class ChainError(DiffSegError):
    """备份链断裂：某个备份声明的基准版本缺失或不匹配。"""


class MissingBaseError(DiffSegError):
    """恢复时缺少基准内容，空洞无法填充。"""


class DigestMismatchError(DiffSegError):
    """恢复结果与备份记录的目标摘要不一致。"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Segment:
    """一个差异段：基准版本 + 偏移 + 内容（长度与摘要由内容推出）。"""

    base_version: str
    offset: int
    data: bytes

    @property
    def length(self) -> int:
        return len(self.data)

    @property
    def end(self) -> int:
        return self.offset + len(self.data)

    @property
    def digest(self) -> str:
        return _sha256(self.data)


@dataclass
class Backup:
    """一份差异备份：同一备份内的段不得互相覆盖。"""

    version: str
    base_version: str
    target_length: int
    target_digest: str
    segments: list = field(default_factory=list)

    def validate(self) -> None:
        if not isinstance(self.target_length, int) or self.target_length < 0:
            raise ValidationError(f"{self.version}: 非法目标长度 {self.target_length!r}")
        if len(self.target_digest) != 64:
            raise ValidationError(f"{self.version}: 非法目标摘要 {self.target_digest!r}")
        prev_end = 0
        prev_idx = -1
        for idx, seg in enumerate(self.segments):
            if seg.base_version != self.base_version:
                raise ValidationError(
                    f"{self.version}#{idx}: 段基准版本 {seg.base_version!r} "
                    f"与备份基准版本 {self.base_version!r} 不一致"
                )
            if seg.offset < 0:
                raise ValidationError(f"{self.version}#{idx}: 负偏移 {seg.offset}")
            if seg.length == 0:
                raise ValidationError(f"{self.version}#{idx}: 空段")
            if seg.offset < prev_end:
                raise ValidationError(
                    f"{self.version}: 同一备份内段互相覆盖: "
                    f"段 #{prev_idx} 结束于 {prev_end}, 段 #{idx} 起始于 {seg.offset}"
                )
            if seg.end > self.target_length:
                raise ValidationError(
                    f"{self.version}#{idx}: 段越界 [{seg.offset},{seg.end}) "
                    f"超过目标长度 {self.target_length}"
                )
            prev_end = seg.end
            prev_idx = idx


def encode(base: bytes, target: bytes, base_version: str, version: str,
           block_size: int = DEFAULT_BLOCK_SIZE) -> Backup:
    """把 base -> target 的差异编码成一份备份。

    按 block_size 分块对比，连续发生变化（或新增）的块合并为一个段；
    target 比 base 短时通过 target_length 截断，不产生段。
    """
    if block_size <= 0:
        raise ValueError("block_size 必须为正整数")
    segments = []
    i = 0
    n = len(target)
    while i < n:
        blk_end = min(i + block_size, n)
        base_blk = base[i:blk_end] if i < len(base) else b""
        if target[i:blk_end] == base_blk:
            i = blk_end
            continue
        start = i
        chunks = []
        while i < n:
            blk_end = min(i + block_size, n)
            base_blk = base[i:blk_end] if i < len(base) else b""
            blk = target[i:blk_end]
            if blk == base_blk:
                break
            chunks.append(blk)
            i = blk_end
        segments.append(Segment(base_version=base_version, offset=start,
                                data=b"".join(chunks)))
    backup = Backup(version=version, base_version=base_version,
                    target_length=n, target_digest=_sha256(target),
                    segments=segments)
    backup.validate()
    return backup


# ---------------------------------------------------------------- 序列化

def backup_to_dict(backup: Backup) -> dict:
    return {
        "format": FORMAT,
        "version": backup.version,
        "base_version": backup.base_version,
        "target_length": backup.target_length,
        "target_digest": backup.target_digest,
        "segments": [
            {
                "base_version": seg.base_version,
                "offset": seg.offset,
                "length": seg.length,
                "sha256": seg.digest,
                "data_b64": base64.b64encode(seg.data).decode("ascii"),
            }
            for seg in backup.segments
        ],
    }


def backup_from_dict(raw: dict) -> Backup:
    if raw.get("format") != FORMAT:
        raise ValidationError(f"未知格式: {raw.get('format')!r}")
    segments = []
    for idx, item in enumerate(raw.get("segments", [])):
        data = base64.b64decode(item["data_b64"])
        if len(data) != item["length"]:
            raise ValidationError(
                f"段 #{idx}: 声明长度 {item['length']} 与内容长度 {len(data)} 不符")
        if _sha256(data) != item["sha256"]:
            raise ValidationError(f"段 #{idx}: 内容摘要校验失败")
        segments.append(Segment(base_version=item["base_version"],
                                offset=item["offset"], data=data))
    backup = Backup(version=raw["version"], base_version=raw["base_version"],
                    target_length=raw["target_length"],
                    target_digest=raw["target_digest"], segments=segments)
    backup.validate()
    return backup


def dumps_backup(backup: Backup) -> str:
    return json.dumps(backup_to_dict(backup), ensure_ascii=False, indent=2)


def loads_backup(text: str) -> Backup:
    return backup_from_dict(json.loads(text))


def save_backup(backup: Backup, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps_backup(backup))


def load_backup(path: str) -> Backup:
    with open(path, "r", encoding="utf-8") as fh:
        return loads_backup(fh.read())


# ---------------------------------------------------------------- 恢复

@dataclass(frozen=True)
class OverlapRecord:
    """一处重叠：旧段 covered_segment 的 [offset, offset+length) 被新段覆盖。"""

    covered_segment: str
    covering_segment: str
    offset: int
    length: int


@dataclass(frozen=True)
class HoleRecord:
    """一个空洞：未被任何段覆盖，由基准版本内容填充。"""

    offset: int
    length: int
    filled_from: str


@dataclass
class RestoreReport:
    target_version: str
    target_length: int
    applied_segments: list = field(default_factory=list)   # 有字节落地的段
    covered_segments: list = field(default_factory=list)   # 被完全覆盖的段
    overlaps: list = field(default_factory=list)           # OverlapRecord
    holes: list = field(default_factory=list)              # HoleRecord
    digest_ok: bool = False


def report_to_dict(report: RestoreReport) -> dict:
    return {
        "target_version": report.target_version,
        "target_length": report.target_length,
        "applied_segments": list(report.applied_segments),
        "covered_segments": list(report.covered_segments),
        "overlaps": [
            {"covered_segment": o.covered_segment,
             "covering_segment": o.covering_segment,
             "offset": o.offset, "length": o.length}
            for o in report.overlaps
        ],
        "holes": [
            {"offset": h.offset, "length": h.length, "filled_from": h.filled_from}
            for h in report.holes
        ],
        "digest_ok": report.digest_ok,
    }


def _free_ranges(occupied: list, start: int, end: int) -> list:
    """返回 [start, end) 中尚未被 occupied（有序、互不重叠）覆盖的子区间。"""
    out = []
    cur = start
    for iv_start, iv_end, _owner in occupied:
        if iv_end <= cur:
            continue
        if iv_start >= end:
            break
        if iv_start > cur:
            out.append((cur, min(iv_start, end)))
        cur = max(cur, iv_end)
        if cur >= end:
            break
    if cur < end:
        out.append((cur, end))
    return out


def restore(base_content, backups, base_version: str = None):
    """按备份链恢复最新版本，返回 (字节内容, RestoreReport)。

    base_content: 基准版本（backups[0].base_version）的完整内容；链上无空洞
        覆盖不到的地方都要从它取字节。为 None 时若出现空洞则抛 MissingBaseError。
    backups: 按时间从旧到新排列的 Backup 列表。
    base_version: 可选，声明 base_content 对应的版本号，用于校验链的根部。
    """
    backups = list(backups)
    for backup in backups:
        backup.validate()

    if not backups:
        if base_content is None:
            raise MissingBaseError("没有任何备份且缺少基准内容")
        digest = _sha256(base_content)
        report = RestoreReport(
            target_version=base_version or "<base>",
            target_length=len(base_content),
            digest_ok=True,
        )
        return bytes(base_content), report

    if base_version is not None and backups[0].base_version != base_version:
        raise ChainError(
            f"基准缺失: 链根需要基准 {backups[0].base_version!r}, "
            f"但提供的基准是 {base_version!r}")
    for prev, cur in zip(backups, backups[1:]):
        if cur.base_version != prev.version:
            raise ChainError(
                f"基准缺失: {cur.version} 的基准版本是 {cur.base_version!r}, "
                f"但链上前一版本是 {prev.version!r}")

    target_length = backups[-1].target_length
    expected_digest = backups[-1].target_digest
    result = bytearray(target_length)

    occupied = []  # (start, end, owner_seg_id)，按 start 有序、互不重叠
    overlaps = []
    applied = []
    covered = []

    # 最新优先：先处理新备份的段，旧段只能填充尚未被占用的区间。
    for backup in reversed(backups):
        for idx, seg in enumerate(backup.segments):
            seg_id = f"{backup.version}#{idx}"
            # 旧备份的段可能超出最终目标长度（之后被截断），裁剪到目标范围内。
            seg_start = max(seg.offset, 0)
            seg_end = min(seg.end, target_length)
            if seg_start >= seg_end:
                covered.append(seg_id)
                continue
            for iv_start, iv_end, owner in occupied:
                if iv_end <= seg_start or iv_start >= seg_end:
                    continue
                ov_start = max(iv_start, seg_start)
                ov_end = min(iv_end, seg_end)
                overlaps.append(OverlapRecord(
                    covered_segment=seg_id, covering_segment=owner,
                    offset=ov_start, length=ov_end - ov_start))
            pieces = _free_ranges(occupied, seg_start, seg_end)
            if not pieces:
                covered.append(seg_id)
                continue
            applied.append(seg_id)
            for p_start, p_end in pieces:
                result[p_start:p_end] = seg.data[p_start - seg.offset:
                                                 p_end - seg.offset]
                bisect.insort(occupied, (p_start, p_end, seg_id))

    # 空洞：未被任何段覆盖的区间，显式记录并只能用基准内容填充。
    hole_ranges = []
    cur = 0
    for iv_start, iv_end, _owner in occupied:
        if iv_start > cur:
            hole_ranges.append((cur, iv_start))
        cur = max(cur, iv_end)
    if cur < target_length:
        hole_ranges.append((cur, target_length))

    holes = []
    missing = []
    for h_start, h_end in hole_ranges:
        if base_content is None or h_end > len(base_content):
            missing.append((h_start, h_end))
        else:
            result[h_start:h_end] = base_content[h_start:h_end]
            holes.append(HoleRecord(offset=h_start, length=h_end - h_start,
                                    filled_from=backups[0].base_version))
    if missing:
        desc = ", ".join(f"[{s},{e})" for s, e in missing)
        raise MissingBaseError(f"基准缺失或基准过短，无法填充空洞: {desc}")

    data = bytes(result)
    if _sha256(data) != expected_digest:
        raise DigestMismatchError(
            f"恢复结果摘要与 {backups[-1].version} 记录的目标摘要不一致")

    overlaps.sort(key=lambda o: (o.offset, o.covered_segment))
    report = RestoreReport(
        target_version=backups[-1].version,
        target_length=target_length,
        applied_segments=applied,
        covered_segments=covered,
        overlaps=overlaps,
        holes=holes,
        digest_ok=True,
    )
    return data, report
