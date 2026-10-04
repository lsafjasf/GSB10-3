"""Append-only log-structured index (the kind of index that bloats).

Every put/delete appends a record to the log file. Deletes are tombstones.
Overwrites and deletes leave dead records behind, so the file grows while
the amount of live data does not -- that is index bloat.

Record layout (big-endian):
    op:      1 byte   (1 = put, 0 = delete/tombstone)
    key_len: 2 bytes
    val_len: 4 bytes  (0 for deletes)
    key:     key_len bytes
    value:   val_len bytes

An in-memory table maps key -> (offset, record_length) for live records,
so point lookups are O(1). All public operations take a single RLock,
which is also what makes the online rebuild's atomic swap possible.
"""

import os
import struct
import threading

OP_DEL = 0
OP_PUT = 1

HEADER = struct.Struct(">BHI")
HEADER_SIZE = HEADER.size


def record_length(key_len, val_len):
    return HEADER_SIZE + key_len + val_len


class LogIndex:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._table = {}          # key(bytes) -> (offset, record_len)
        self._live_bytes = 0      # bytes occupied by live records
        self._total_bytes = 0     # current file size
        if os.path.exists(path):
            self._recover()
        self._fp = open(path, "ab")
        self._reader = open(path, "rb")

    # ------------------------------------------------------------------
    # recovery / stats
    # ------------------------------------------------------------------
    def _recover(self):
        off = 0
        with open(self.path, "rb") as f:
            while True:
                header = f.read(HEADER_SIZE)
                if len(header) < HEADER_SIZE:
                    break
                op, klen, vlen = HEADER.unpack(header)
                key = f.read(klen)
                f.read(vlen)
                rlen = record_length(klen, vlen)
                self._apply_to_table(key, off, rlen, op)
                off += rlen
        self._total_bytes = off

    def _apply_to_table(self, key, off, rlen, op):
        old = self._table.get(key)
        if op == OP_PUT:
            if old is None:
                self._live_bytes += rlen
            else:
                self._live_bytes += rlen - old[1]
            self._table[key] = (off, rlen)
        else:  # tombstone: the tombstone itself is dead weight
            if old is not None:
                self._live_bytes -= old[1]
                del self._table[key]

    def stats(self):
        """(total_bytes, live_bytes, live_entries), taken under the lock."""
        with self._lock:
            return self._total_bytes, self._live_bytes, len(self._table)

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------
    def put(self, key: bytes, value: bytes):
        rec = HEADER.pack(OP_PUT, len(key), len(value)) + key + value
        with self._lock:
            off = self._total_bytes
            self._fp.write(rec)
            self._fp.flush()
            self._apply_to_table(key, off, len(rec), OP_PUT)
            self._total_bytes += len(rec)

    def delete(self, key: bytes):
        rec = HEADER.pack(OP_DEL, len(key), 0) + key
        with self._lock:
            off = self._total_bytes
            self._fp.write(rec)
            self._fp.flush()
            self._apply_to_table(key, off, len(rec), OP_DEL)
            self._total_bytes += len(rec)

    def get(self, key: bytes):
        with self._lock:
            loc = self._table.get(key)
            if loc is None:
                return None
            off, _ = loc
            self._reader.seek(off)
            header = self._reader.read(HEADER_SIZE)
            _, klen, vlen = HEADER.unpack(header)
            self._reader.read(klen)
            return self._reader.read(vlen)

    def keys(self):
        with self._lock:
            return list(self._table.keys())

    def close(self):
        with self._lock:
            self._fp.close()
            self._reader.close()

    # ------------------------------------------------------------------
    # hooks used by the online rebuilder
    # ------------------------------------------------------------------
    def snapshot(self):
        """(cut_offset, shallow copy of the live table) under the lock.

        Records below cut_offset are immutable, so the rebuilder can copy
        them without holding the lock.
        """
        with self._lock:
            return self._total_bytes, dict(self._table)

    def end_offset(self):
        with self._lock:
            return self._total_bytes

    def lock(self):
        return self._lock

    def _install_rebuilt(self, tmp_path, new_table):
        """Atomically swap the rebuilt file in. Caller holds self._lock."""
        self._fp.flush()
        self._fp.close()
        self._reader.close()
        os.replace(tmp_path, self.path)  # atomic on the same filesystem
        self._fp = open(self.path, "ab")
        self._reader = open(self.path, "rb")
        self._table = new_table
        self._total_bytes = os.path.getsize(self.path)
        self._live_bytes = sum(rlen for _, rlen in new_table.values())
