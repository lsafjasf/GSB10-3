"""Online (lock-free while copying) rebuild of a bloated LogIndex.

Protocol
--------
1. SNAPSHOT: take cut_offset and a copy of the live table under the index
   lock (instant). The old index keeps serving reads AND writes the whole
   time until the final swap.
2. COPY: write all live records (read from their immutable offsets below
   cut_offset in the old file) into a brand-new compacted file. No lock is
   held, so writes keep flowing into the old file.
3. CATCH-UP: replay records appended after cut_offset into the new file:
   puts are appended, tombstones just drop the key. Repeat while the
   un-replayed tail is large.
4. SWAP: take the index lock (blocking new writes for the short tail),
   replay the final small tail, run a full old-vs-new differential check,
   then atomically install the new file with os.replace().

If anything fails (including an injected crash for tests), the temp file
is removed and the old index is untouched: a failed rebuild is invisible
to readers and can simply be retried.
"""

import os

from .log_index import HEADER, HEADER_SIZE, OP_DEL, OP_PUT


class RebuildInterrupted(Exception):
    """Injected/real failure during a rebuild; the old index is intact."""


class RebuildVerificationFailed(RuntimeError):
    """Old and new indexes disagreed; the swap was not performed."""


def iter_records(path, start, end):
    """Yield (op, key, value, raw, length) for records in [start, end)."""
    with open(path, "rb") as f:
        f.seek(start)
        off = start
        while off < end:
            header = f.read(HEADER_SIZE)
            if len(header) < HEADER_SIZE:
                break
            op, klen, vlen = HEADER.unpack(header)
            key = f.read(klen)
            value = f.read(vlen)
            length = HEADER_SIZE + klen + vlen
            yield op, key, value, header + key + value, length
            off += length


def read_value_at(path, off):
    with open(path, "rb") as f:
        f.seek(off)
        _, klen, vlen = HEADER.unpack(f.read(HEADER_SIZE))
        f.read(klen)
        return f.read(vlen)


class OnlineRebuilder:
    """One shot per rebuild: OnlineRebuilder(index).run()."""

    CATCHUP_TAIL = 4096  # bytes; stop catching up once the tail is this small

    def __init__(self, index, tmp_path=None):
        self.index = index
        self.tmp_path = tmp_path or (index.path + ".rebuild")
        self.checked = 0
        self.mismatches = 0
        self.copy_bytes = 0
        self.replayed_bytes = 0
        self._new_table = None
        self._caught = 0
        self._out = None

    def run(self, fail_at=None):
        try:
            cut, snapshot = self.index.snapshot()
            self._new_table = snapshot
            self._out = open(self.tmp_path, "wb")
            self._copy_live(cut, fail_at)
            self._caught = self._catch_up(cut, fail_at)
            self._final_swap(fail_at)
        except Exception:
            if self._out is not None:
                self._out.close()
                self._out = None
            if self.tmp_path and os.path.exists(self.tmp_path):
                os.unlink(self.tmp_path)
            raise
        finally:
            if self._out is not None:
                self._out.close()
        return self

    # ------------------------------------------------------------------
    # phase 2: bulk copy of live records (no index lock held)
    # ------------------------------------------------------------------
    def _copy_live(self, cut, fail_at):
        src = open(self.index.path, "rb")
        try:
            for key, (off, rlen) in list(self._new_table.items()):
                src.seek(off)
                raw = src.read(rlen)
                new_off = self._out.tell()
                self._out.write(raw)
                self._new_table[key] = (new_off, rlen)
                self.copy_bytes += rlen
                if fail_at == "copy" and self.copy_bytes > rlen * 16:
                    raise RebuildInterrupted("injected crash during copy phase")
            self._out.flush()
        finally:
            src.close()

    # ------------------------------------------------------------------
    # phase 3: drain the write delta until the tail is small
    # ------------------------------------------------------------------
    def _catch_up(self, caught, fail_at):
        if fail_at == "catchup":
            raise RebuildInterrupted("injected crash during catch-up phase")
        while True:
            end = self.index.end_offset()
            if end - caught <= self.CATCHUP_TAIL:
                return caught
            caught = self._replay(caught, end)

    def _replay(self, start, end):
        for op, key, value, raw, length in iter_records(
            self.index.path, start, end
        ):
            if op == OP_PUT:
                new_off = self._out.tell()
                self._out.write(raw)
                self._new_table[key] = (new_off, length)
            else:
                self._new_table.pop(key, None)
            self.replayed_bytes += length
        self._out.flush()
        return end

    # ------------------------------------------------------------------
    # phase 4: lock, final tail replay, differential check, atomic swap
    # ------------------------------------------------------------------
    def _final_swap(self, fail_at):
        with self.index.lock():
            end = self.index.end_offset()
            self._replay(self._caught, end)

            self.checked, self.mismatches = differential_check(
                self.index, self.tmp_path, self._new_table
            )
            if self.mismatches:
                raise RebuildVerificationFailed(
                    "%d mismatches out of %d keys"
                    % (self.mismatches, self.checked)
                )
            if fail_at == "verify":
                raise RebuildInterrupted("injected crash after verification")

            self.index._install_rebuilt(self.tmp_path, self._new_table)
            self.tmp_path = None  # successfully consumed; don't delete it


def differential_check(index, new_path, new_table):
    """Compare every key between the old (live) and new index.

    Returns (checked, mismatches). Must be called while the caller holds
    the index lock so the old state cannot move under us.
    """
    old_keys = set(index.keys())
    new_keys = set(new_table)
    checked = 0
    mismatches = 0

    for key in old_keys | new_keys:
        old_val = index.get(key)
        loc = new_table.get(key)
        new_val = read_value_at(new_path, loc[0]) if loc is not None else None
        checked += 1
        if old_val != new_val:
            mismatches += 1
    return checked, mismatches
