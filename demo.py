"""Tamper-localization demo (run: python3 demo.py).

Scenarios: single record modified, consecutive records modified, tail
deletion, middle deletion.  Each scenario shows the full-verify result,
a range-verify result, and that range verification agrees with full
verification while touching only the records between anchors.
"""

import json
import os
import shutil
import tempfile

from auditlog import AuditLog

K = 10
N = 40


def rewrite_records(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def show(title, result):
    status = "OK" if result.ok else f"FAIL positions={result.positions}"
    print(f"  {title:32s} -> {status}")
    if not result.ok:
        for err in result.errors:
            print(f"      seq={err.position:<3d} {err.kind:8s} {err.message}")


def fresh_log(path):
    log = AuditLog(path, checkpoint_interval=K)
    for n in range(1, N + 1):
        log.append({"event": "pay", "amount": n * 10, "n": n}, ts=float(n))
    return AuditLog(path, checkpoint_interval=K)


def main():
    root = tempfile.mkdtemp(prefix="auditlog-demo-")
    try:
        # 1. single record modified
        p = os.path.join(root, "single.log")
        log = fresh_log(p)
        recs = log.records
        recs[14]["data"]["amount"] = 99999
        rewrite_records(p, recs)
        log = AuditLog(p, checkpoint_interval=K)
        print("[1] single record tampered (seq 15, amount changed)")
        show("full verify", log.verify_full())
        show("range verify [11, 20]", log.verify_range(11, 20))
        show("range verify [21, 30] (clean)", log.verify_range(21, 30))

        # 2. consecutive records modified
        p = os.path.join(root, "multi.log")
        log = fresh_log(p)
        recs = log.records
        for idx in (24, 25, 26):
            recs[idx]["data"]["event"] = "forged"
        rewrite_records(p, recs)
        log = AuditLog(p, checkpoint_interval=K)
        print("[2] consecutive records tampered (seq 25-27)")
        show("full verify", log.verify_full())
        show("range verify [23, 28]", log.verify_range(23, 28))

        # 3. tail deletion
        p = os.path.join(root, "tail.log")
        log = fresh_log(p)
        recs = log.records[:-2]
        rewrite_records(p, recs)
        log = AuditLog(p, checkpoint_interval=K)
        print("[3] tail deletion (seq 39-40 removed)")
        show("full verify", log.verify_full())
        show("range verify [31, 40]", log.verify_range(31, 40))

        # 4. middle deletion
        p = os.path.join(root, "middle.log")
        log = fresh_log(p)
        recs = log.records
        del recs[19:22]                       # remove seq 20-22
        rewrite_records(p, recs)
        log = AuditLog(p, checkpoint_interval=K)
        print("[4] middle deletion (seq 20-22 removed)")
        show("full verify", log.verify_full())
        show("range verify [16, 25]", log.verify_range(16, 25))

        # 5. clean chain: range == full
        p = os.path.join(root, "clean.log")
        log = fresh_log(p)
        print("[5] untampered log")
        show("full verify", log.verify_full())
        show("range verify [5, 7]", log.verify_range(5, 7))
        print("  consistency assertion: full.ok == every range.ok -> "
              f"{log.verify_full().ok == all(log.verify_range(i, j).ok for i in range(1, N + 1) for j in range(i, N + 1))}")
        print(f"\nfiles written under {root}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
