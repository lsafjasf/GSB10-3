"""全量 / 增量导出。

一致性保证：
- 「当前位点」与「数据行」在同一个只读事务中读取（SQLite 快照隔离），
  因此导出文件与文件头记录的 to_version 严格对应同一时刻。
- 导出文件先原子落盘并 fsync，位点文件随后才推进；崩溃在两者之间时，
  下次会从旧位点重导出一段重叠范围，按 version 幂等应用即可，不丢不重。
- 无位点（首次导出 / 位点丢失 / 位点损坏）时自动回退为全量导出。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import checkpoint as ckpt_mod
from .source import current_seq

FULL = "full"
INCREMENTAL = "incremental"


@dataclass(frozen=True)
class ExportResult:
    kind: str             # full / incremental
    path: str             # 导出文件路径
    from_version: int
    to_version: int
    record_count: int     # 文件中的记录数（含墓碑）
    elapsed_seconds: float
    fallback_reason: str = ""  # 非空表示因位点缺失/损坏回退了全量


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_jsonl_atomic(path: str, header: dict, rows_iter) -> int:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".export-", suffix=".tmp")
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(header, ensure_ascii=False) + "\n")
            for row in rows_iter:
                f.write(json.dumps({
                    "id": row[0],
                    "payload": row[1],
                    "version": row[2],
                    "deleted": row[3],
                }, ensure_ascii=False) + "\n")
                count += 1
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        ckpt_mod.fsync_dir(directory)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return count


def _export_full(conn: sqlite3.Connection, out_dir: str) -> ExportResult:
    started = time.perf_counter()
    conn.isolation_level = None
    conn.execute("BEGIN")
    try:
        to_version = current_seq(conn)
        path = os.path.join(out_dir, f"full_v{to_version}_{_ts()}.jsonl")
        cursor = conn.execute(
            "SELECT id, payload, version, deleted FROM records "
            "WHERE deleted = 0 ORDER BY id"
        )
        count = _write_jsonl_atomic(path, {
            "type": FULL,
            "from_version": 0,
            "to_version": to_version,
            "created_at": _ts(),
        }, cursor)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return ExportResult(FULL, path, 0, to_version, count,
                        time.perf_counter() - started)


def _export_incremental(conn: sqlite3.Connection, out_dir: str,
                        from_version: int) -> ExportResult:
    started = time.perf_counter()
    conn.isolation_level = None
    conn.execute("BEGIN")
    try:
        to_version = current_seq(conn)
        path = os.path.join(
            out_dir, f"inc_v{from_version}_v{to_version}_{_ts()}.jsonl")
        # idx_records_version 保证大范围位点查询也是索引区间扫描，
        # 无变化时只做一次轻量探测，O(变化量) 而非 O(总量)。
        cursor = conn.execute(
            "SELECT id, payload, version, deleted FROM records "
            "WHERE version > ? ORDER BY version, id",
            (from_version,))
        count = _write_jsonl_atomic(path, {
            "type": INCREMENTAL,
            "from_version": from_version,
            "to_version": to_version,
            "created_at": _ts(),
        }, cursor)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return ExportResult(INCREMENTAL, path, from_version, to_version, count,
                        time.perf_counter() - started)


def run_export(conn: sqlite3.Connection, checkpoint_path: str,
               out_dir: str) -> ExportResult:
    """按位点状态自动选择全量或增量；导出落盘后才推进位点。"""
    ckpt = ckpt_mod.load(checkpoint_path)
    if ckpt is None:
        reason = "无可用位点（首次导出或位点丢失/损坏），回退全量导出"
        result = _export_full(conn, out_dir)
        result = ExportResult(result.kind, result.path, result.from_version,
                              result.to_version, result.record_count,
                              result.elapsed_seconds, reason)
    else:
        result = _export_incremental(conn, out_dir, ckpt.version)

    # 关键顺序：导出文件已 fsync 落盘后，才持久化新位点。
    ckpt_mod.save(checkpoint_path, ckpt_mod.Checkpoint(
        version=result.to_version,
        export_kind=result.kind,
        export_file=os.path.basename(result.path),
        updated_at=ckpt_mod.now_iso(),
    ))
    return result
