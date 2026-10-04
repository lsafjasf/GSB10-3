#!/usr/bin/env python3
"""增量导出工具（仅标准库）。

模型：
- 源库 (SQLite) 中每行带单调递增的 version；删除写入 tombstone。
- 位点 (checkpoint) 持久化上次已导出的 version，原子写入，崩溃可恢复。
- 全量导出 = version <= 高水位 V 的全部行（确定性快照，与并发写入无关）。
- 增量导出 = (上次位点, 当前高水位] 区间内的 upsert + delete。
- 物化回放 materialize(基线全量 + 增量序列) 必须与同时刻全量导出字节级一致（对拍）。
"""
import json
import os
import sqlite3
import tempfile
import time


# ---------------------------------------------------------------- 源库

class SourceDB:
    """模拟业务源库：items 表 + tombstones 表 + 全局 version 计数器。"""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v INTEGER);
            CREATE TABLE IF NOT EXISTS items(
                id TEXT PRIMARY KEY, payload TEXT, version INTEGER);
            CREATE INDEX IF NOT EXISTS idx_items_ver ON items(version);
            CREATE TABLE IF NOT EXISTS tombstones(id TEXT PRIMARY KEY, version INTEGER);
            CREATE INDEX IF NOT EXISTS idx_tomb_ver ON tombstones(version);
            INSERT OR IGNORE INTO meta(k, v) VALUES('version', 0);
            """
        )
        self.conn.commit()

    def max_version(self):
        """当前高水位，O(1)。"""
        return self.conn.execute(
            "SELECT v FROM meta WHERE k='version'").fetchone()[0]

    def bulk_upsert(self, rows):
        """rows: [(id, payload), ...]，单事务、单调 version。"""
        if not rows:
            return
        base = self.max_version()
        with self.conn:
            self.conn.executemany(
                "INSERT INTO items(id, payload, version) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,"
                " version=excluded.version",
                [(i, p, base + n) for n, (i, p) in enumerate(rows, 1)])
            self.conn.executemany(
                "DELETE FROM tombstones WHERE id=?", [(i,) for i, _ in rows])
            self.conn.execute("UPDATE meta SET v=? WHERE k='version'",
                              (base + len(rows),))

    def bulk_delete(self, ids):
        if not ids:
            return
        base = self.max_version()
        with self.conn:
            self.conn.executemany("DELETE FROM items WHERE id=?",
                                  [(i,) for i in ids])
            self.conn.executemany(
                "INSERT INTO tombstones(id, version) VALUES(?,?) "
                "ON CONFLICT(id) DO UPDATE SET version=excluded.version",
                [(i, base + n) for n, i in enumerate(ids, 1)])
            self.conn.execute("UPDATE meta SET v=? WHERE k='version'",
                              (base + len(ids),))

    def fetch_snapshot(self, upto):
        """version <= upto 的全量行，按 id 排序（确定性输出）。"""
        return self.conn.execute(
            "SELECT id, payload FROM items WHERE version<=? ORDER BY id",
            (upto,)).fetchall()

    def fetch_upserts(self, since, upto):
        return self.conn.execute(
            "SELECT id, payload FROM items WHERE version>? AND version<=? "
            "ORDER BY version", (since, upto)).fetchall()

    def fetch_deletes(self, since, upto):
        return self.conn.execute(
            "SELECT id FROM tombstones WHERE version>? AND version<=? "
            "ORDER BY version", (since, upto)).fetchall()


# ---------------------------------------------------------------- 位点

class CheckpointStore:
    """位点持久化：tmp 文件 + fsync + os.replace 原子落盘，崩溃后旧位点仍有效。"""

    def __init__(self, path):
        self.path = path

    def load(self):
        """返回已持久化的 version；文件缺失/损坏/半写入时返回 None（触发全量兜底）。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            v = int(data["version"])
            if v < 0:
                raise ValueError("negative version")
            return v
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, version):
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".ckpt-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": version, "saved_at": time.time()}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)  # 原子生效：崩溃只会留下 tmp 残文件
            dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------- 导出

def _write_snapshot(path, version, rows):
    """全量快照的规范写法：header + 按 id 排序的行。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "full", "version": version}) + "\n")
        for rid, payload in rows:
            f.write(json.dumps({"id": rid, "payload": payload}) + "\n")


class Exporter:
    def __init__(self, db, checkpoint_path):
        self.db = db
        self.ckpt = CheckpointStore(checkpoint_path)

    def export_full(self, out_path):
        """先取高水位 V，再导出 version<=V 的行 —— 与并发写入无关的确定性快照。"""
        v = self.db.max_version()
        rows = self.db.fetch_snapshot(v)
        _write_snapshot(out_path, v, rows)
        self.ckpt.save(v)
        return {"kind": "full", "version": v, "rows": len(rows)}

    def export_incremental(self, out_path):
        """导出 (位点, 高水位] 的变更；无位点时兜底为全量；无变化时快速返回空增量。"""
        v0 = self.ckpt.load()
        if v0 is None:
            info = self.export_full(out_path)
            info["fallback"] = "no valid checkpoint"
            return info
        v1 = self.db.max_version()
        if v1 == v0:
            # 快速路径：高水位未推进，不触碰 items/tombstones，直接写空增量
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "delta", "from": v0, "to": v1}) + "\n")
            self.ckpt.save(v1)
            return {"kind": "delta", "from": v0, "to": v1, "ops": 0}
        upserts = self.db.fetch_upserts(v0, v1)
        deletes = self.db.fetch_deletes(v0, v1)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "delta", "from": v0, "to": v1}) + "\n")
            for rid, payload in upserts:
                f.write(json.dumps({"op": "upsert", "id": rid,
                                    "payload": payload}) + "\n")
            for (rid,) in deletes:
                f.write(json.dumps({"op": "delete", "id": rid}) + "\n")
        self.ckpt.save(v1)
        return {"kind": "delta", "from": v0, "to": v1,
                "ops": len(upserts) + len(deletes)}


def materialize(full_path, delta_paths, out_path):
    """把基线全量 + 依次应用增量，物化为当前快照（用于对拍/下游回放）。

    upsert/delete 按 id 幂等，崩溃重试重复应用同一增量结果不变。
    """
    state = {}
    version = None
    with open(full_path, "r", encoding="utf-8") as f:
        header = json.loads(f.readline())
        assert header["type"] == "full"
        version = header["version"]
        for line in f:
            rec = json.loads(line)
            state[rec["id"]] = rec["payload"]
    for dp in delta_paths:
        with open(dp, "r", encoding="utf-8") as f:
            header = json.loads(f.readline())
            assert header["type"] == "delta"
            version = header["to"]
            for line in f:
                rec = json.loads(line)
                if rec["op"] == "upsert":
                    state[rec["id"]] = rec["payload"]
                elif rec["op"] == "delete":
                    state.pop(rec["id"], None)
    _write_snapshot(out_path, version, sorted(state.items()))
    return version


# ---------------------------------------------------------------- CLI

def main(argv):
    import sys
    if len(argv) < 4:
        print("usage:\n"
              "  exporter.py full     <db> <ckpt> <out.jsonl>\n"
              "  exporter.py incr     <db> <ckpt> <out.jsonl>\n"
              "  exporter.py materialize <base.jsonl> <out.jsonl> <delta...>",
              file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd in ("full", "incr"):
        db_path, ckpt_path, out_path = argv[2], argv[3], argv[4]
        exp = Exporter(SourceDB(db_path), ckpt_path)
        info = (exp.export_full if cmd == "full"
                else exp.export_incremental)(out_path)
        print(json.dumps(info, ensure_ascii=False))
        return 0
    if cmd == "materialize":
        base, out, deltas = argv[2], argv[3], argv[4:]
        v = materialize(base, deltas, out)
        print(json.dumps({"materialized_version": v}))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
