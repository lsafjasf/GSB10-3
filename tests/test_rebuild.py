"""Self tests for read-only paged-store index rebuild.

Run: python3 -m unittest -v tests.test_rebuild   (from repo root)
  or python3 tests/test_rebuild.py
"""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from paged_store import (  # noqa: E402
    HEADER_SIZE,
    PagedStoreWriter,
    dump_index_json,
    load_index_json,
    lookup,
    rebuild_index,
)

PAGE_SIZE = 256  # small, so a few records already span multiple pages


def write_store(path, entries, page_size=PAGE_SIZE):
    """entries: list of (key, value); returns the authoritative in-memory index."""
    with PagedStoreWriter(path, page_size=page_size) as w:
        for key, value in entries:
            w.put(key, value)
        return dict(w.index)


def corrupt_at(path, file_offset, length=8):
    with open(path, "r+b") as fp:
        fp.seek(file_offset)
        original = fp.read(length)
        fp.seek(file_offset)
        fp.write(bytes(b ^ 0xFF for b in original))


class RebuildIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "store.dat")

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_file(self):
        open(self.path, "wb").close()
        index, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual(index, {})
        self.assertEqual(report.pages_scanned, 0)
        self.assertEqual(report.corrupt_pages, [])
        self.assertEqual(report.conflict_list(), [])
        self.assertIsNone(lookup(self.path, index, "anything", page_size=PAGE_SIZE))

    def test_all_pages_corrupt(self):
        write_store(self.path, [(f"k{i}", f"v{i}") for i in range(12)])
        size = os.path.getsize(self.path)
        for off in range(0, size, PAGE_SIZE):
            corrupt_at(self.path, off + HEADER_SIZE + 1, 4)
        index, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual(index, {})
        self.assertGreater(report.pages_scanned, 0)
        self.assertEqual(len(report.corrupt_pages), report.pages_scanned)
        for c in report.corrupt_pages:
            self.assertEqual(c["offset"], c["page"] * PAGE_SIZE)
        self.assertTrue(all("checksum" in c["reason"] for c in report.corrupt_pages))

    def test_duplicate_keys_across_pages(self):
        # Each value is long enough to force page flips, so duplicates land on
        # pages with strictly larger page numbers.
        entries = []
        for i in range(6):
            entries.append((f"dup-{i % 2}", f"old-{i}-" + "x" * 60))
        entries.append(("dup-0", "newest-0-" + "y" * 60))
        entries.append(("dup-1", "newest-1-" + "y" * 60))
        original = write_store(self.path, entries)

        index, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual(set(index), {b"dup-0", b"dup-1"})

        conflicts = report.conflict_list()
        self.assertEqual({c["key"] for c in conflicts}, {"dup-0", "dup-1"})
        for c in conflicts:
            self.assertGreaterEqual(len(c["occurrences"]), 2)
            pages = [o["page"] for o in c["occurrences"]]
            self.assertEqual(pages, sorted(pages))
            self.assertEqual(c["winner"]["page"], max(pages))
            self.assertEqual(index[c["key"].encode()]["page"], c["winner"]["page"])

        # Winner value is the latest write.
        self.assertEqual(lookup(self.path, index, "dup-0", page_size=PAGE_SIZE),
                         entries[-2][1].encode())
        self.assertEqual(lookup(self.path, index, "dup-1", page_size=PAGE_SIZE),
         entries[-1][1].encode())
        # Rebuilt index agrees with the writer's authoritative index.
        self.assertEqual(index, original)

    def test_duplicate_on_same_page_later_record_wins(self):
        with PagedStoreWriter(self.path, page_size=PAGE_SIZE) as w:
            w.put("k", "first")
            w.put("k", "second")
            original = dict(w.index)
        index, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual(index, original)
        self.assertEqual(lookup(self.path, index, "k", page_size=PAGE_SIZE), b"second")
        self.assertEqual(report.conflict_list()[0]["winner"]["page"], 0)

    def test_inconsistent_page_size(self):
        # Truncate one byte off the last page -> partial / short trailing page.
        # Pad values so the file spans several pages.
        write_store(self.path, [(f"k{i}", "v" + "x" * 60) for i in range(12)])
        size = os.path.getsize(self.path)
        with open(self.path, "r+b") as fp:
            fp.truncate(size - 1)
        index, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual(len(report.corrupt_pages), 1)
        bad = report.corrupt_pages[0]
        self.assertEqual(bad["offset"], size - PAGE_SIZE)
        self.assertIn("partial page", bad["reason"])
        # Earlier, intact pages still contribute keys.
        self.assertGreaterEqual(len(index), 1)

    def test_lookup_consistent_before_and_after_rebuild(self):
        entries = [(f"alpha-{i}", f"payload-{i}-" + "z" * 40) for i in range(15)]
        entries += [(f"alpha-{i % 5}", f"updated-{i}") for i in range(5, 10)]
        original_index = write_store(self.path, entries)

        all_keys = sorted({k for k, _ in entries})
        # Lookups against the original (pre-crash) index.
        before = {k: lookup(self.path, original_index, k, page_size=PAGE_SIZE)
                  for k in all_keys}
        before["missing-key"] = lookup(
            self.path, original_index, "missing-key", page_size=PAGE_SIZE)

        # Simulate "index lost": rebuild purely from the data pages.
        rebuilt, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual(rebuilt, original_index)

        # Lookups against the rebuilt index must match exactly.
        after = {k: lookup(self.path, rebuilt, k, page_size=PAGE_SIZE) for k in all_keys}
        after["missing-key"] = lookup(
            self.path, rebuilt, "missing-key", page_size=PAGE_SIZE)
        self.assertEqual(after, before)

        # The rebuilt index also survives a JSON round-trip.
        reloaded = load_index_json(dump_index_json(rebuilt))
        self.assertEqual(
            {k: lookup(self.path, reloaded, k, page_size=PAGE_SIZE) for k in all_keys},
            {k: before[k] for k in all_keys},
        )

    def test_corrupt_page_skipped_scan_continues(self):
        # Build 4 pages; corrupt page 1 only.
        entries = [(f"p{i // 3:02d}-key-{i}", f"v{i}-" + "q" * 50)
                   for i in range(12)]
        write_store(self.path, entries)
        corrupt_at(self.path, PAGE_SIZE + HEADER_SIZE + 2, 6)

        index, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual([c["page"] for c in report.corrupt_pages], [1])
        self.assertEqual(report.corrupt_pages[0]["offset"], PAGE_SIZE)
        # Keys on pages 0, 2, 3 are still recovered.
        recovered = {k.decode() for k in index}
        self.assertIn("p00-key-0", recovered)
        self.assertTrue(any(k.startswith("p02") or k.startswith("p03")
                            for k in recovered))
        self.assertFalse(any(k.startswith("p01") for k in recovered))

    def test_bad_magic_isolated(self):
        write_store(self.path, [("ok", "v")])
        with open(self.path, "r+b") as fp:
            fp.seek(0)
            fp.write(b"XXXX")
        index, report = rebuild_index(self.path, page_size=PAGE_SIZE)
        self.assertEqual(index, {})
        self.assertEqual(report.corrupt_pages[0]["reason"], "bad magic")

    def test_scan_does_not_open_file_writable(self):
        # Point the read at an in-memory-style read-only file object via fd.
        write_store(self.path, [("ro", "value")])
        fd = os.open(self.path, os.O_RDONLY)
        try:
            with os.fdopen(fd, "rb", closefd=False) as fp:
                index, _ = rebuild_index(self.path, PAGE_SIZE, fp=fp)
        finally:
            os.close(fd)
        self.assertEqual(lookup(self.path, index, "ro", page_size=PAGE_SIZE), b"value")


if __name__ == "__main__":
    unittest.main(verbosity=2)
