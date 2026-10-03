"""Self-tests for the read-only index rebuilder.

Run with:
    python3 -m unittest discover -s tests -v
"""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pagestore import (
    HEADER_SIZE,
    MAGIC,
    StoreWriter,
    decode_records,
    encode_page,
    encode_records,
    rebuild_index,
    scan_file,
)
from pagestore.format import header_candidate
from pagestore.rebuild import RebuiltIndex

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO_ROOT, "rebuild_index.py")


class RebuildTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "store.pst")

    def tearDown(self):
        self.tmp.cleanup()

    def _expected_from_pages(self, data, surviving_offsets):
        """Independent reference map: apply the winner rule to raw surviving
        pages, reusing the decoder but not any scanner/index logic."""
        from pagestore.format import decode_page

        winner = {}
        for offset in surviving_offsets:
            page_no, _ps, payload, _end = decode_page(data, offset)
            rec_off = offset + HEADER_SIZE
            for idx, (key, value) in enumerate(decode_records(payload)):
                if key not in winner or (page_no, idx) >= winner[key][0]:
                    winner[key] = ((page_no, idx), value, rec_off)
                rec_off += 2 + len(key) + 4 + len(value)
        return {k: v[1] for k, v in winner.items()}

    # 1. empty file -------------------------------------------------------
    def test_empty_file(self):
        open(self.path, "wb").close()
        result = scan_file(self.path)
        self.assertEqual(result.file_size, 0)
        self.assertEqual(result.valid_pages, [])
        self.assertEqual(result.corrupt_regions, [])
        self.assertEqual(result.conflicts, [])
        self.assertEqual(len(result.index), 0)
        self.assertIsNone(result.index.lookup(b"anything"))

    # 2. every page corrupt ----------------------------------------------
    def test_all_pages_corrupt(self):
        with open(self.path, "wb") as fh:
            fh.write(b"\x00" * 64)
            fh.write(b"GARBAGE" * 10)
            # structurally complete page but flipped payload -> CRC failure
            fh.write(encode_page(0, encode_records([(b"k", b"v")]))[:30] + b"X")
            # torn page: magic + truncated header
            fh.write(MAGIC + b"\x00\x01")
        result = scan_file(self.path)
        self.assertEqual(result.valid_pages, [])
        self.assertEqual(len(result.index), 0)
        self.assertTrue(len(result.corrupt_regions) >= 1)
        self.assertEqual(
            sum(r.length for r in result.corrupt_regions), result.file_size
        )
        self.assertNotEqual(result.report_dict()["summary"]["pages_valid"], -1)

    def test_scan_raises_on_missing_file_only(self):
        with self.assertRaises(FileNotFoundError):
            scan_file(os.path.join(self.tmp.name, "nope.pst"))

    # 3. duplicate keys across pages (incl. out-of-order page numbers) ----
    def test_duplicate_keys_across_pages(self):
        with StoreWriter(self.path) as w:
            w.append_page([(b"a", b"v0"), (b"b", b"old")])
            w.append_page([(b"a", b"v1"), (b"c", b"keep")])
            # same-page duplicate: later record must win
            w.append_page([(b"b", b"v2-first"), (b"b", b"v2")])
            # page number 3 written, then a page with smaller page number:
            # winner rule is by page_no, not physical scan order
            w._fh.write(encode_page(7, encode_records([(b"a", b"v7")])))
            w._fh.write(encode_page(4, encode_records([(b"a", b"v4"), (b"c", b"c4")])))
            w._page_no = 8
        result = scan_file(self.path)
        idx = result.index
        self.assertEqual(idx.lookup(b"a"), b"v7")
        self.assertEqual(idx.lookup(b"b"), b"v2")
        self.assertEqual(idx.lookup(b"c"), b"c4")

        conflicts = {c.key: c for c in result.conflicts}
        self.assertEqual(set(conflicts), {b"a", b"b", b"c"})
        # full conflict list: every superseded occurrence must be present
        ca = conflicts[b"a"]
        self.assertEqual(ca.winner.page_no, 7)
        self.assertEqual([o.page_no for o in ca.losers], [0, 1, 4])
        self.assertEqual([o.value for o in ca.losers], [b"v0", b"v1", b"v4"])
        self.assertEqual(len(ca.losers), 3)
        cb = conflicts[b"b"]
        self.assertEqual(cb.winner.record_index, 1)
        self.assertEqual(cb.losers[0].page_no, 0)
        self.assertEqual(cb.losers[1].record_index, 0)
        # report must be JSON-serialisable and carry the full lists
        report = result.report_dict()
        text = json.dumps(report)
        self.assertIn("v1", text)
        self.assertEqual(report["summary"]["duplicate_occurrences"], 6)

    # 4. mixed page sizes -------------------------------------------------
    def test_mixed_page_sizes(self):
        with open(self.path, "wb") as fh:
            fh.write(encode_page(0, encode_records([(b"k1", b"x")]), page_size=32))
            fh.write(encode_page(1, encode_records([(b"k2", b"y" * 5000)]), page_size=9000))
            fh.write(encode_page(2, encode_records([(b"k3", b"")]), page_size=1024))
        result = scan_file(self.path)
        sizes = sorted(p.page_size for p in result.valid_pages)
        self.assertEqual(sizes, [32, 1024, 9000])
        idx = result.index
        self.assertEqual(idx.lookup(b"k1"), b"x")
        self.assertEqual(idx.lookup(b"k2"), b"y" * 5000)
        self.assertEqual(idx.lookup(b"k3"), b"")
        self.assertEqual(result.corrupt_regions, [])

    # 5. corrupt page in the middle is skipped, offsets recorded ----------
    def test_corrupt_middle_page_skipped_with_offsets(self):
        good1 = encode_page(0, encode_records([(b"before", b"1")]))
        good_page_body = encode_page(3, encode_records([(b"after", b"9"), (b"a", b"v3")]))
        bad = encode_page(1, encode_records([(b"lost", b"zzz"), (b"a", b"v1")]))
        bad = bad[: HEADER_SIZE + 3] + bytes([bad[HEADER_SIZE + 3] ^ 0xFF]) + bad[HEADER_SIZE + 4 :]
        with open(self.path, "wb") as fh:
            fh.write(good1)
            bad_off = len(good1)
            fh.write(bad)
            fh.write(b"junk\xff\xfe")
            good_off = bad_off + len(bad) + len(b"junk\xff\xfe")
            fh.write(good_page_body)
        self.assertIsNone(header_candidate(open(self.path, "rb").read(), bad_off))

        result = scan_file(self.path)
        surviving = [0, good_off]
        data = open(self.path, "rb").read()
        expected = self._expected_from_pages(data, surviving)

        self.assertEqual([p.page_no for p in result.valid_pages], [0, 3])
        idx = result.index
        for key, value in expected.items():
            self.assertEqual(idx.lookup(key), value, key)
        self.assertNotIn(b"lost", idx)
        # 'a' survives only from page 3 -> v3, and conflict list reflects
        # that page 1 never contributed
        self.assertEqual(idx.lookup(b"a"), b"v3")
        self.assertEqual(result.conflicts, [])

        regions = result.corrupt_regions
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].offset, bad_off)
        self.assertEqual(regions[0].length, len(bad) + len(b"junk\xff\xfe"))
        self.assertIn("crc", regions[0].reason)

        # every corrupt byte range must lie inside the file, ranges partition
        # the unreadable bytes without touching good pages
        covered = sum(r.length for r in regions)
        good_bytes = sum(p.payload_len + HEADER_SIZE for p in result.valid_pages)
        self.assertEqual(covered + good_bytes, result.file_size)

    # 6. resync past magic-looking garbage and trailing junk --------------
    def test_resync_past_magic_looking_garbage(self):
        decoy = MAGIC + b"\x00" * 40  # magic present but CRC cannot validate
        with open(self.path, "wb") as fh:
            fh.write(b"###" + decoy + b"!!")
            off = fh.tell()
            fh.write(encode_page(5, encode_records([(b"real", b"data")])))
            fh.write(b"\x7f" * 7)  # trailing garbage
            total = fh.tell()
        result = scan_file(self.path)
        self.assertEqual([p.page_no for p in result.valid_pages], [5])
        self.assertEqual(result.valid_pages[0].offset, off)
        self.assertEqual(result.index.lookup(b"real"), b"data")
        offsets = [r.offset for r in result.corrupt_regions]
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets[1], total - 7)

    # 7. lookup consistency before vs after rebuild -----------------------
    def test_lookup_consistency_clean_file(self):
        keys = [("k%02d" % i).encode() for i in range(50)]
        with StoreWriter(self.path) as w:
            for chunk in range(5):
                w.append_page([(k, b"v-%s" % k) for k in keys[10 * chunk : 10 * chunk + 10]])
            reference = dict(w.index)
        rebuilt = rebuild_index(self.path)
        # every key resolves identically through the rebuilt index...
        for key, value in reference.items():
            self.assertEqual(rebuilt.lookup(key), value)
        # ...including miss semantics
        self.assertIsNone(rebuilt.lookup(b"k99"))
        self.assertNotIn(b"k99", rebuilt)

    def test_lookup_consistency_corrupted_file(self):
        # corrupt a known page; expected answers are derived independently
        # from the page list the test itself created.
        pages = [
            encode_page(0, encode_records([(b"a", b"0"), (b"b", b"0")])),
            encode_page(1, encode_records([(b"a", b"1"), (b"c", b"1")])),
            encode_page(2, encode_records([(b"a", b"2"), (b"d", b"2")])),
        ]
        offsets = []
        with open(self.path, "wb") as fh:
            for p in pages:
                offsets.append(fh.tell())
                fh.write(p)
        # nuke page 1 (offset +1 byte so its magic dies)
        size1 = len(pages[1])
        with open(self.path, "r+b") as fh:
            fh.seek(offsets[1])
            fh.write(b"\xDE" * size1)
        data = open(self.path, "rb").read()
        expected = self._expected_from_pages(data, [offsets[0], offsets[2]])
        result = scan_file(self.path)
        for key, value in expected.items():
            self.assertEqual(result.index.lookup(key), value)
        # page-1-only key must be reported missing, not wrongly resolved
        self.assertIsNone(result.index.lookup(b"c"))
        # index entry offsets allow reading the record directly from the file
        self.assertEqual(result.index.get(self.path, b"d"), b"2")
        self.assertIsNone(result.index.entry(b"c"))

    # 8. persisted index round-trip --------------------------------------
    def test_index_save_load_roundtrip(self):
        with StoreWriter(self.path) as w:
            w.append_page([(b"k", b"v"), (b"n", b"\x00\xff")])
        idx_path = os.path.join(self.tmp.name, "idx.json")
        rebuilt = rebuild_index(self.path)
        rebuilt.save(idx_path)
        loaded = RebuiltIndex.load(idx_path)
        for key in [b"k", b"n"]:
            self.assertEqual(loaded.lookup(key), rebuilt.lookup(key))
        # loaded index can still verify against the original data file
        self.assertEqual(loaded.get(self.path, b"n"), b"\x00\xff")
        self.assertIsNone(loaded.lookup(b"missing"))

    # 9. CLI end to end ---------------------------------------------------
    def test_cli_end_to_end(self):
        with StoreWriter(self.path) as w:
            w.append_page([(b"hello", b"world"), (b"dup", b"old")])
            w.append_page([(b"dup", b"new")])
        idx_path = os.path.join(self.tmp.name, "idx.json")
        report_path = os.path.join(self.tmp.name, "report.json")
        proc = subprocess.run(
            [
                sys.executable,
                CLI,
                self.path,
                "--index-out",
                idx_path,
                "--report-out",
                report_path,
                "--get",
                "hello",
                "--get",
                "dup",
                "--get",
                "nope",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("GET hello -> world", proc.stdout)
        self.assertIn("GET dup -> new", proc.stdout)
        self.assertIn("GET nope -> <missing>", proc.stdout)
        report = json.load(open(report_path, encoding="utf-8"))
        self.assertEqual(report["summary"]["pages_valid"], 2)
        self.assertEqual(report["summary"]["conflicting_keys"], 1)
        # saved index is directly reusable without rescanning
        proc2 = subprocess.run(
            [sys.executable, CLI, self.path, "--index-in", idx_path, "--get", "dup"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertIn("GET dup -> new", proc2.stdout)

    # 10. torn write: partial page at EOF ---------------------------------
    def test_torn_trailing_page(self):
        good = encode_page(0, encode_records([(b"ok", b"1")]))
        torn = encode_page(1, encode_records([(b"gone", b"2")]))[:20]
        with open(self.path, "wb") as fh:
            fh.write(good)
            torn_off = len(good)
            fh.write(torn)
        result = scan_file(self.path)
        self.assertEqual([p.page_no for p in result.valid_pages], [0])
        self.assertEqual(len(result.corrupt_regions), 1)
        self.assertEqual(result.corrupt_regions[0].offset, torn_off)
        self.assertEqual(result.index.lookup(b"ok"), b"1")
        self.assertIsNone(result.index.lookup(b"gone"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
