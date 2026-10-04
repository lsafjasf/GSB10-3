"""位点（checkpoint）持久化。

设计要点：
- 位点写入采用「临时文件 + fsync + os.replace + 目录 fsync」，
  崩溃时只会看到完整旧值或完整新值，绝不会看到写了一半的位点。
- 位点文件丢失或损坏时 load() 返回 None，由调用方回退为全量导出。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class Checkpoint:
    version: int        # 已成功导出的最大版本号（位点）
    export_kind: str    # 产生该位点的导出类型：full / incremental
    export_file: str    # 产生该位点的导出文件名
    updated_at: str     # ISO-8601 时间戳

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "export_kind": self.export_kind,
            "export_file": self.export_file,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "Checkpoint":
        version = data["version"]
        if not isinstance(version, int) or version < 0:
            raise ValueError(f"非法位点版本号: {version!r}")
        return Checkpoint(
            version=version,
            export_kind=str(data["export_kind"]),
            export_file=str(data["export_file"]),
            updated_at=str(data["updated_at"]),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load(path: str) -> Optional[Checkpoint]:
    """读取位点；文件不存在或内容损坏时返回 None（调用方回退全量）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return Checkpoint.from_dict(json.load(f))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(path: str, ckpt: Checkpoint) -> None:
    """原子写入位点，保证崩溃后可恢复到旧值或新值。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ckpt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ckpt.to_dict(), f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # 同目录原子替换
        fsync_dir(directory)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
