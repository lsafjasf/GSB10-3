"""Replication log puller.

Guarantees
----------
- The confirmed position (meaning "every position < confirmed is consumed")
  is persisted once per batch, atomically (temp file + fsync + os.replace).
- After restart the puller resumes exactly at the confirmed position.
- A purged position raises GapError and appends a gap report line; the
  puller never silently advances to the first available position.
- Every fetch has a socket timeout and is retried with linear backoff.
- Redelivered batches are detected against the confirmed position and
  skipped (full duplicate) or trimmed (partial overlap); duplicates are
  counted in ``duplicate_batches``.
"""

import json
import os
import socket
import time


class GapError(Exception):
    """Raised when the requested position was already purged upstream."""

    def __init__(self, requested, first_available):
        self.requested = requested
        self.first_available = first_available
        super().__init__(
            "gap detected: requested position %d has been purged; "
            "first available position is %d; missing interval [%d, %d)"
            % (requested, first_available, requested, first_available)
        )


class Puller:
    def __init__(self, host, port, checkpoint_path, consume,
                 batch_size=10, start_position=0, timeout=1.0,
                 max_retries=5, backoff=0.05, gap_report_path=None):
        self.host = host
        self.port = port
        self.checkpoint_path = checkpoint_path
        self.consume = consume
        self.batch_size = batch_size
        self.start_position = start_position
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.gap_report_path = gap_report_path or checkpoint_path + ".gaps.jsonl"
        self.duplicate_batches = 0
        self.retries = 0
        self.confirmed = self._load_checkpoint()

    # ---- checkpoint -------------------------------------------------------

    def _load_checkpoint(self):
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as fh:
                return int(json.load(fh)["confirmed"])
        except FileNotFoundError:
            return self.start_position

    def _save_checkpoint(self):
        tmp_path = self.checkpoint_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({"confirmed": self.confirmed}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.checkpoint_path)  # atomic on POSIX/Windows

    # ---- transport --------------------------------------------------------

    def _request(self, payload):
        with socket.create_connection((self.host, self.port),
                                      timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("connection closed by source")
                buf += chunk
            return json.loads(buf)

    def _fetch(self, start):
        attempt = 0
        while True:
            try:
                return self._request({"start": start, "max_batch": self.batch_size})
            except (OSError, ConnectionError):
                # socket.timeout and ConnectionError are OSError subclasses.
                attempt += 1
                if attempt > self.max_retries:
                    raise
                self.retries += 1  # counts actual retries, not the 1st attempt
                time.sleep(self.backoff * attempt)

    # ---- gap --------------------------------------------------------------

    def _report_gap(self, error):
        report = {
            "event": "gap",
            "requested": error.requested,
            "first_available": error.first_available,
            "missing_interval": [error.requested, error.first_available],
            "timestamp": time.time(),
        }
        with open(self.gap_report_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(report) + "\n")
        return report

    # ---- main loop --------------------------------------------------------

    def pull_batch(self):
        """Fetch and consume one batch. Returns False when caught up."""
        response = self._fetch(self.confirmed)
        kind = response["type"]

        if kind == "gap":
            error = GapError(response["requested"], response["first_available"])
            self._report_gap(error)
            raise error

        if kind == "up_to_date":
            return False

        start = response["start"]
        end = response["end"]
        records = response["records"]

        # Dedup against the confirmed position.
        if end <= self.confirmed:
            self.duplicate_batches += 1          # fully redelivered batch
            return True
        if start < self.confirmed:
            self.duplicate_batches += 1          # partial redelivery
            records = records[self.confirmed - start:]

        for record in records:
            self.consume(self.confirmed, record)
            self.confirmed += 1
        self._save_checkpoint()                  # one fsync per batch
        return True

    def run_until_caught_up(self, max_batches=10000):
        batches = 0
        while batches < max_batches and self.pull_batch():
            batches += 1
        return batches
