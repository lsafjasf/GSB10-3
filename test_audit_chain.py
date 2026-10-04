"""Self-tests for auditlog.audit_chain (stdlib unittest only).

Covers: single-record tamper, consecutive multi-record tamper, tail
deletion, middle deletion, range-vs-full verification consistency, and
boundary cases.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auditlog import AuditLog, GENESIS, hash_record

N = 100          # records per log
K = 10           # checkpoint interval


def rewrite_records(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="auditlog-test-")
        self.path = os.path.join(self.dir, "audit.log")
        log = AuditLog(self.path, checkpoint_interval=K)
        for n in range(1, N + 1):
            log.append({"event": "login", "user": f"u{n}", "n": n}, ts=float(n))
        self.log = AuditLog(self.path, checkpoint_interval=K)  # reopen

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def tamper_data(self, seq, new_data):
        records = self.log.records
        records[seq - 1]["data"] = new_data
        rewrite_records(self.path, records)

    def delete_record(self, seq):
        records = self.log.records
        del records[seq - 1]
        rewrite_records(self.path, records)


class TestCleanLog(Base):
    def test_full_verify_ok(self):
        self.assertTrue(self.log.verify_full().ok)

    def test_chain_binding(self):
        records = self.log.records
        self.assertEqual(records[0]["prev"], GENESIS)
        for prev, cur in zip(records, records[1:]):
            self.assertEqual(cur["prev"], prev["hash"])
            self.assertEqual(hash_record(cur), cur["hash"])

    def test_anchors(self):
        cps = self.log.checkpoints
        self.assertEqual(cps[0], {"seq": 0, "hash": GENESIS})
        self.assertEqual([c["seq"] for c in cps],
                         [0] + list(range(K, N + 1, K)))
        self.assertEqual(self.log.head["seq"], N)
        for c in cps[1:]:
            self.assertEqual(c["hash"], self.log.records[c["seq"] - 1]["hash"])


class TestSingleTamper(Base):
    def test_locate_single_tamper(self):
        self.tamper_data(42, {"event": "login", "user": "root"})
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        self.assertEqual(full.positions, [42])          # exact localization
        self.assertEqual(full.errors[0].kind, "content")
        rng = self.log.verify_range(40, 55)             # range containing it
        self.assertFalse(rng.ok)
        self.assertEqual(rng.positions, [42])
        self.assertTrue(self.log.verify_range(50, 70).ok)  # outside range ok

    def test_tamper_with_recomputed_hash_still_detected(self):
        # Attacker modifies data AND recomputes the record hash.
        records = self.log.records
        rec = records[41]
        rec["data"] = {"event": "sudo", "user": "root"}
        rec["hash"] = hash_record(rec)
        rewrite_records(self.path, records)
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        # Record #43 still points at the old hash of #42, so the link
        # breaks exactly at the boundary after the tampered record.
        self.assertEqual(full.positions, [43])

    def test_full_rechain_after_tamper_stopped_by_anchor(self):
        # Attacker modifies #42 and recomputes every hash/link afterwards.
        records = self.log.records
        records[41]["data"] = {"event": "sudo", "user": "root"}
        prev_hash = GENESIS
        for rec in records:
            rec["prev"] = prev_hash
            rec["hash"] = hash_record(rec)
            prev_hash = rec["hash"]
        rewrite_records(self.path, records)
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        # Every checkpoint from seq 50 on rejects the forged chain;
        # the first one localizes the attack to the segment 41..50.
        self.assertEqual(full.first_position, 50)
        self.assertEqual(full.errors[0].kind, "anchor")
        rng = self.log.verify_range(41, 45)        # anchor-scoped range catches
        self.assertFalse(rng.ok)


class TestMultiTamper(Base):
    def test_locate_consecutive_tamper(self):
        for seq in (30, 31, 32):
            self.tamper_data(seq, {"event": "deleted-by-attacker"})
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        self.assertEqual(full.positions, [30, 31, 32])
        rng = self.log.verify_range(28, 35)
        self.assertFalse(rng.ok)
        self.assertEqual(rng.positions, [30, 31, 32])


class TestTailDeletion(Base):
    def test_tail_delete_detected(self):
        self.delete_record(N)               # remove last record
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        self.assertEqual(full.first_position, N)
        self.assertEqual(full.errors[0].kind, "gap")
        self.assertIn("tail deleted", full.errors[0].message)
        rng = self.log.verify_range(N - 5, N)
        self.assertFalse(rng.ok)
        self.assertEqual(rng.first_position, N)

    def test_tail_delete_partial_segment(self):
        # Delete the last 3 records; tail anchor (head) still exposes it.
        for _ in range(3):
            self.delete_record(len(self.log.records))
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        self.assertEqual(full.first_position, N - 2)


class TestMiddleDeletion(Base):
    def test_middle_delete_detected_with_gap_position(self):
        self.delete_record(55)
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        kinds = {e.kind for e in full.errors}
        self.assertIn("gap", kinds)
        gap = next(e for e in full.errors if e.kind == "gap")
        self.assertEqual(gap.position, 55)  # exact gap location
        self.assertIn("55..55", gap.message)
        rng = self.log.verify_range(50, 60)
        self.assertFalse(rng.ok)
        self.assertEqual(rng.first_position, 55)

    def test_middle_delete_multiple(self):
        records = self.log.records
        del records[69:74]                  # delete seq 70..74
        rewrite_records(self.path, records)
        full = self.log.verify_full()
        self.assertFalse(full.ok)
        gap = next(e for e in full.errors if e.kind == "gap")
        self.assertEqual(gap.position, 70)
        self.assertIn("70..74", gap.message)


class TestRangeEqualsFull(Base):
    """Range verification must agree with full verification."""

    def test_clean_log_all_ranges_ok(self):
        self.assertTrue(self.log.verify_full().ok)
        for i in range(1, N + 1):
            for j in range(i, N + 1):
                self.assertTrue(self.log.verify_range(i, j).ok,
                                f"range [{i},{j}] should verify")

    def test_tampered_log_range_matches_full(self):
        for bad in (7, 50, 93):
            self.tamper_data(bad, {"event": "forged"})
        full = self.log.verify_full()
        self.assertEqual(full.positions, [7, 50, 93])
        for i in range(1, N + 1, 3):
            for j in range(i, N + 1, 3):
                rng = self.log.verify_range(i, j)
                expected_bad = [p for p in full.positions if i <= p <= j]
                if expected_bad:
                    self.assertFalse(rng.ok, f"range [{i},{j}]")
                    self.assertEqual(rng.positions, expected_bad,
                                     f"range [{i},{j}]")
                else:
                    self.assertTrue(rng.ok, f"range [{i},{j}]")

    def test_range_does_not_recompute_full_chain(self):
        # Spy on hash_record calls: range verify must touch only the
        # records between the surrounding anchors, not all N.
        import auditlog.audit_chain as ac
        calls = []
        orig = ac.hash_record
        ac.hash_record = lambda rec: (calls.append(rec["seq"]),
                                      orig(rec))[1]
        try:
            self.assertTrue(self.log.verify_range(41, 55).ok)
        finally:
            ac.hash_record = orig
        self.assertLessEqual(max(calls), 60)     # upper anchor at seq 60
        self.assertGreaterEqual(min(calls), 41)  # lower anchor at seq 40
        self.assertLess(len(calls), N)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="auditlog-edge-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, name="audit.log", k=10):
        return AuditLog(os.path.join(self.dir, name), checkpoint_interval=k)

    def test_empty_log(self):
        log = self.make()
        self.assertTrue(log.verify_full().ok)
        with self.assertRaises(ValueError):
            log.verify_range(1, 1)

    def test_single_record(self):
        log = self.make()
        log.append({"event": "only"}, ts=1.0)
        self.assertTrue(log.verify_full().ok)
        self.assertTrue(log.verify_range(1, 1).ok)
        records = log.records
        records[0]["data"]["event"] = "forged"
        rewrite_records(log.path, records)
        self.assertEqual(log.verify_full().positions, [1])
        self.assertEqual(log.verify_range(1, 1).positions, [1])

    def test_range_boundaries(self):
        log = self.make(k=4)
        for n in range(1, 11):
            log.append({"n": n}, ts=float(n))
        self.assertTrue(log.verify_range(1, 1).ok)          # first record
        self.assertTrue(log.verify_range(10, 10).ok)        # last record
        self.assertTrue(log.verify_range(1, 10).ok)         # whole chain
        self.assertTrue(log.verify_range(4, 5).ok)          # across anchor
        self.assertTrue(log.verify_range(9, 10).ok)         # tail, head anchor
        with self.assertRaises(ValueError):
            log.verify_range(5, 4)
        with self.assertRaises(ValueError):
            log.verify_range(0, 3)

    def test_first_record_tamper(self):
        log = self.make(k=4)
        for n in range(1, 6):
            log.append({"n": n}, ts=float(n))
        records = log.records
        records[0]["data"] = {"n": "forged"}
        rewrite_records(log.path, records)
        self.assertEqual(log.verify_full().positions, [1])
        self.assertEqual(log.verify_range(1, 3).positions, [1])

    def test_invalid_checkpoint_interval(self):
        with self.assertRaises(ValueError):
            self.make(k=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
