"""数据源模拟：SQLite 表 + 全局单调递增版本号。

每条记录带 version（最后一次变更对应的版本号）和 deleted 墓碑标记，
每次变更（insert/update/delete）都会推进全局版本号，从而可以用
「version > 上次位点」精确界定增量范围。删除保留墓碑，增量才能传播删除。
"""
from __future__ import annotations

import random
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    id      TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_records_version ON records(version);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('seq', 0) "
        "ON CONFLICT(key) DO NOTHING"
    )
    conn.commit()
    return conn


def current_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='seq'").fetchone()
    return int(row[0])


def _next_version(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE meta SET value = value + 1 WHERE key='seq'")
    return current_seq(conn)


def _random_payload(rng: random.Random, size: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(rng.choices(alphabet, k=size))


def init_db(conn: sqlite3.Connection, rows: int, payload_size: int = 48,
            seed: int = 42) -> None:
    """批量生成初始数据，每条记录一个独立版本号。"""
    rng = random.Random(seed)
    with conn:
        for i in range(rows):
            version = _next_version(conn)
            conn.execute(
                "INSERT INTO records(id, payload, version, deleted) "
                "VALUES (?, ?, ?, 0)",
                (f"rec-{i:08d}", _random_payload(rng, payload_size), version),
            )


def mutate(conn: sqlite3.Connection, changes: int, seed: int = 1,
           update_ratio: float = 0.6, delete_ratio: float = 0.2) -> dict:
    """随机变更 changes 次：更新 / 删除（墓碑）/ 新增。返回各类操作次数。"""
    rng = random.Random(seed)
    stats = {"update": 0, "delete": 0, "insert": 0}
    alive = [r[0] for r in conn.execute(
        "SELECT id FROM records WHERE deleted = 0")]
    with conn:
        for _ in range(changes):
            op = rng.random()
            version = _next_version(conn)
            if alive and op < update_ratio:
                idx = rng.randrange(len(alive))
                rid = alive[idx]
                conn.execute(
                    "UPDATE records SET payload=?, version=?, deleted=0 WHERE id=?",
                    (_random_payload(rng, 48), version, rid))
                stats["update"] += 1
            elif alive and op < update_ratio + delete_ratio:
                idx = rng.randrange(len(alive))
                rid = alive[idx]
                conn.execute(
                    "UPDATE records SET deleted=1, version=? WHERE id=?", (version, rid))
                alive[idx] = alive[-1]
                alive.pop()
                stats["delete"] += 1
            else:
                rid = f"rec-new-{version:012d}"
                conn.execute(
                    "INSERT INTO records(id, payload, version, deleted) "
                    "VALUES (?, ?, ?, 0)",
                    (rid, _random_payload(rng, 48), version))
                alive.append(rid)
                stats["insert"] += 1
    return stats
