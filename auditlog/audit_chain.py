"""Tamper-evident audit log: hash chain + checkpoint anchors + range verification.

Only Python standard library is used.

Design
------
* Every record carries ``prev`` (hash of the previous record) and its own
  ``hash`` over the canonical form of (seq, ts, data, prev).  This forms a
  hash chain: modifying any record breaks its own content hash and/or the
  link to the next record.
* Every ``checkpoint_interval`` records an anchor ``{"seq", "hash"}`` is
  appended to a sidecar checkpoint file, and a ``head`` anchor (latest
  seq/hash) is maintained.  Anchors are the trusted roots: they let you
  verify any range [i, j] by recomputing only the records between the
  surrounding anchors instead of the whole chain.
* Deletions are detected as sequence-number gaps, broken ``prev`` links, or
  a tail that no longer reaches the next anchor.

Files
-----
``<path>``               JSONL records, one per line.
``<path>.checkpoints``   JSONL anchors, genesis anchor {"seq": 0} first.
``<path>.head``          JSON anchor of the latest record (tail anchor).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

GENESIS = "0" * 64  # prev-hash of record #1 / hash committed by anchor seq 0

RECORD_CONTENT_KEYS = ("seq", "ts", "data", "prev")


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def hash_record(record: dict) -> str:
    """Content hash of a record over (seq, ts, data, prev)."""
    content = {k: record[k] for k in RECORD_CONTENT_KEYS}
    return hashlib.sha256(_canonical(content)).hexdigest()


def _read_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _append_jsonl(path: str, obj: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


@dataclass
class VerifyError:
    position: int          # 1-based seq where the problem is located
    kind: str              # 'content' | 'link' | 'gap' | 'anchor'
    message: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"[seq={self.position}] {self.kind}: {self.message}"


@dataclass
class VerifyResult:
    errors: List[VerifyError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def positions(self) -> List[int]:
        """Sorted positions at which problems were found."""
        return sorted({e.position for e in self.errors})

    @property
    def first_position(self) -> Optional[int]:
        return self.positions[0] if self.errors else None

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if self.ok:
            return "OK"
        return "TAMPERED: " + "; ".join(str(e) for e in self.errors)


def _check_segment(records: Iterable[dict], start_seq: int, start_hash: str,
                   errors: List[VerifyError]) -> tuple:
    """Check records (must have consecutive seq starting at start_seq).

    Returns (next_expected_seq, last_stored_hash).  ``start_hash`` is the
    trusted hash committed by the anchor at ``start_seq - 1``.
    """
    expected = start_seq
    prev_stored = start_hash
    for rec in records:
        seq = rec["seq"]
        if seq != expected:
            errors.append(VerifyError(
                expected, "gap",
                f"missing record(s) {expected}..{seq - 1} (deleted)"))
            expected = seq
        if rec["prev"] != prev_stored:
            errors.append(VerifyError(
                seq, "link",
                "prev-hash does not match previous record's hash "
                "(record before this one was deleted or replaced)"))
        if hash_record(rec) != rec["hash"]:
            errors.append(VerifyError(
                seq, "content",
                "stored hash does not match recomputed hash "
                "(record content tampered)"))
        prev_stored = rec["hash"]
        expected = seq + 1
    return expected, prev_stored


class AuditLog:
    """A hash-chained, checkpoint-anchored audit log."""

    def __init__(self, path: str, checkpoint_interval: int = 10):
        if checkpoint_interval < 1:
            raise ValueError("checkpoint_interval must be >= 1")
        self.path = path
        self.checkpoint_interval = checkpoint_interval
        self.checkpoints_path = path + ".checkpoints"
        self.head_path = path + ".head"
        if not os.path.exists(self.checkpoints_path):
            _append_jsonl(self.checkpoints_path, {"seq": 0, "hash": GENESIS})
        if not os.path.exists(self.head_path):
            with open(self.head_path, "w", encoding="utf-8") as fh:
                json.dump({"seq": 0, "hash": GENESIS}, fh)

    # -- writing ---------------------------------------------------------

    def append(self, data: Any, ts: Optional[float] = None) -> dict:
        head = self.head
        record = {
            "seq": head["seq"] + 1,
            "ts": time.time() if ts is None else ts,
            "data": data,
            "prev": head["hash"],
        }
        record["hash"] = hash_record(record)
        _append_jsonl(self.path, record)
        if record["seq"] % self.checkpoint_interval == 0:
            _append_jsonl(self.checkpoints_path,
                          {"seq": record["seq"], "hash": record["hash"]})
        with open(self.head_path, "w", encoding="utf-8") as fh:
            json.dump({"seq": record["seq"], "hash": record["hash"]}, fh)
        return record

    # -- reading ---------------------------------------------------------

    @property
    def records(self) -> List[dict]:
        return _read_jsonl(self.path)

    @property
    def checkpoints(self) -> List[dict]:
        return _read_jsonl(self.checkpoints_path)

    @property
    def head(self) -> dict:
        with open(self.head_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    @property
    def anchors(self) -> List[dict]:
        """Trusted anchors: checkpoints plus the tail (head) anchor."""
        anchors = list(self.checkpoints)
        head = self.head
        if not anchors or anchors[-1]["seq"] < head["seq"]:
            anchors.append(head)
        return anchors

    # -- verification ----------------------------------------------------

    def verify_full(self) -> VerifyResult:
        """Verify the whole chain from genesis.  O(n)."""
        errors: List[VerifyError] = []
        records = self.records
        next_seq, last_hash = _check_segment(records, 1, GENESIS, errors)
        anchor_map = {a["seq"]: a["hash"] for a in self.anchors}
        last_anchor_seq = max(anchor_map)
        if last_anchor_seq >= next_seq:
            errors.append(VerifyError(
                next_seq, "gap",
                f"missing record(s) {next_seq}..{last_anchor_seq} "
                f"(tail deleted)"))
        else:
            for rec in records:
                seq = rec["seq"]
                if seq in anchor_map and anchor_map[seq] != rec["hash"]:
                    errors.append(VerifyError(
                        seq, "anchor",
                        "record hash does not match checkpoint anchor"))
        return VerifyResult(errors)

    def verify_range(self, i: int, j: int) -> VerifyResult:
        """Verify records [i, j] without recomputing the whole chain.

        Cost is O((j - i) + 2 * checkpoint_interval + log #anchors):
        only the records between the anchors surrounding [i, j] are
        re-hashed, and the result is checked against those anchors.
        """
        records = self.records
        if not records:
            raise ValueError("log is empty")
        lo, hi = records[0]["seq"], records[-1]["seq"]
        if i < 1 or j < i:
            raise ValueError(f"invalid range [{i}, {j}]")

        anchors = sorted(self.anchors, key=lambda a: a["seq"])
        # Latest anchor at or before i-1 commits the hash we start from.
        start_anchor = anchors[0]  # genesis anchor, seq 0
        for a in anchors:
            if a["seq"] <= i - 1:
                start_anchor = a
            else:
                break
        # Earliest anchor at or after j commits the hash we must end at.
        end_anchor = next((a for a in anchors if a["seq"] >= j), None)
        end_seq = end_anchor["seq"] if end_anchor else max(j, hi)

        errors: List[VerifyError] = []
        segment = [r for r in records
                   if start_anchor["seq"] < r["seq"] <= end_seq]
        next_seq, last_hash = _check_segment(
            segment, start_anchor["seq"] + 1, start_anchor["hash"], errors)
        if next_seq <= end_seq:
            errors.append(VerifyError(
                next_seq, "gap",
                f"missing record(s) {next_seq}..{end_seq} "
                f"(deleted before anchor)"))
        elif end_anchor and last_hash != end_anchor["hash"]:
            errors.append(VerifyError(
                end_seq, "anchor",
                "recomputed chain does not match anchor "
                f"at seq {end_seq}"))
        # Only records inside [i, j] (plus a failing end anchor) belong to
        # the requested range; problems in the anchor-support records are
        # outside the range and are left to a full verification.
        scoped = [e for e in errors
                  if i <= e.position <= j or e.kind == "anchor"]
        return VerifyResult(scoped)
