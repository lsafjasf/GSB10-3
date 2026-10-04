"""Bloat measurement and the rebuild trigger policy.

Metric
------
    space_efficiency = live_bytes / total_bytes
        live_bytes  = bytes occupied by currently-effective (live) records
        total_bytes = bytes actually occupied by the index file

    1 - space_efficiency is the wasted (garbage) fraction; it covers both
    overwritten old versions and tombstones.

Trigger
-------
Rebuild when BOTH hold:
    1. space_efficiency < 0.5  (equivalently: space amplification
       total/live > 2x, i.e. garbage is bigger than live data)
    2. total_bytes >= MIN_REBUILD_BYTES (absolute-waste floor)

Why 0.5 is the right cutoff
---------------------------
* An online rebuild reads the live data once and writes it once, so its
  cost is proportional to live_bytes, and it reclaims dead_bytes.
  A rebuild pays for itself only when dead_bytes >= live_bytes, i.e.
  efficiency <= 0.5.
* Triggering at 0.5 guarantees worst-case space amplification of 2x --
  the same amortization argument used by LSM-tree leveled compaction and
  by generational garbage collectors that collect when half the heap is
  dead. With cost O(live data) per rebuild and O(dead data) of garbage
  accumulated between rebuilds, total rebuild cost stays linear in the
  amount of data actually written by clients.
* The size floor avoids churning a tiny index where even 90% waste is a
  few kilobytes and a rebuild costs more than it saves.
"""

from dataclasses import dataclass

REBUILD_EFFICIENCY_THRESHOLD = 0.5
MIN_REBUILD_BYTES = 64 * 1024  # don't rebuild indexes smaller than this


@dataclass(frozen=True)
class BloatReport:
    total_bytes: int
    live_bytes: int
    live_entries: int

    @property
    def dead_bytes(self):
        return self.total_bytes - self.live_bytes

    @property
    def efficiency(self):
        return self.live_bytes / self.total_bytes if self.total_bytes else 1.0

    @property
    def garbage_ratio(self):
        return 1.0 - self.efficiency

    @property
    def space_amplification(self):
        return self.total_bytes / self.live_bytes if self.live_bytes else float("inf")

    @property
    def should_rebuild(self):
        return (
            self.total_bytes >= MIN_REBUILD_BYTES
            and self.efficiency < REBUILD_EFFICIENCY_THRESHOLD
        )


def measure(index) -> BloatReport:
    total, live, entries = index.stats()
    return BloatReport(total, live, entries)
