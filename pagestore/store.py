"""Forward-only writer for store files.

The writer keeps the in-memory index while the file is being built; that
index is exactly what a crash can lose, which is the scenario the rebuilder
recovers from.
"""

from .format import encode_page, encode_records


class StoreWriter:
    """Append pages of records to a store file."""

    def __init__(self, path):
        self.path = path
        self._fh = open(path, "wb")
        self._page_no = 0
        self.index = {}

    def append_page(self, records, page_size=None):
        """Write one page; returns the page number used."""
        payload = encode_records(records)
        page = encode_page(self._page_no, payload, page_size=page_size)
        self._fh.write(page)
        for key, value in records:
            self.index[bytes(key)] = bytes(value)
        page_no = self._page_no
        self._page_no += 1
        return page_no

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
