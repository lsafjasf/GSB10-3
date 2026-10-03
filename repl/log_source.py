"""Simulated replication log source.

Positions are non-negative integers; a batch [start, end) covers exactly
the records whose global positions are start..end-1.  Positions are dense,
so "confirmed == N" means every position < N has been consumed.
"""


class LogSource:
    def __init__(self, records, base=0):
        self.base = base
        self.records = list(records)
        # Fault-injection knobs (used by tests and the demo).
        self.drop_connections = 0   # accept then close without responding
        self.hang_connections = 0   # accept but never reply (timeout test)
        self.redeliver_last = False  # replay the previous response once
        self.last_response = None

    @property
    def end(self):
        return self.base + len(self.records)

    @property
    def first_available(self):
        return self.base

    def append(self, records):
        self.records.extend(records)

    def purge_before(self, position):
        """Permanently delete every record with global position < position."""
        drop = max(0, min(position, self.end) - self.base)
        if drop:
            del self.records[:drop]
            self.base += drop

    def fetch(self, start, max_batch):
        if start < self.base:
            # Caller asked for data that no longer exists: explicit gap.
            return {
                "type": "gap",
                "requested": start,
                "first_available": self.base,
            }
        if start >= self.end:
            return {"type": "up_to_date", "end": self.end}
        offset = start - self.base
        batch = self.records[offset:offset + max_batch]
        return {
            "type": "batch",
            "start": start,
            "end": start + len(batch),
            "records": batch,
        }
