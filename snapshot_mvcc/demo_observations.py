"""Deterministic concurrent demo: prints what each snapshot observes.

Run:  python3 demo_observations.py
"""

import threading

from snapshot_store import MVCCStore


def main():
    store = MVCCStore()
    log = []
    log_lock = threading.Lock()

    def record(tag, txn, key="acct"):
        with log_lock:
            log.append((tag, txn.snapshot.snapshot_id, txn.snapshot.boundary,
                        txn.read(key)))

    # t0: initial balance committed.
    seed = store.begin()
    seed.write("acct", 100)
    seed.commit()

    # Long transaction starts; its snapshot is fixed at boundary=1.
    long_txn = store.begin()
    record("long  @t0", long_txn)

    # Barriers make the interleaving deterministic:
    # writer commits -> short reader observes -> long reader re-observes.
    gate_writer = threading.Barrier(2)
    gate_short = threading.Barrier(2)
    gate_long = threading.Barrier(2)

    def writer(value):
        gate_writer.wait()
        txn = store.begin()
        txn.write("acct", value)
        txn.commit()

    def short_reader(tag):
        gate_short.wait()
        txn = store.begin()
        record(tag, txn)
        txn.abort()
        gate_long.wait()

    def run_round(value, tag):
        w = threading.Thread(target=writer, args=(value,))
        s = threading.Thread(target=short_reader, args=(tag,))
        w.start(); s.start()
        gate_writer.wait()          # let writer commit
        w.join()
        gate_short.wait()           # let short reader observe
        gate_long.wait()            # release short reader
        s.join()
        record(f"long  @t{value}", long_txn)

    run_round(80, "short #1")
    run_round(60, "short #2")

    print(f"{'observer':<10} {'snapshot':>8} {'boundary':>8} {'acct':>6}")
    print("-" * 38)
    for tag, snap_id, boundary, value in log:
        print(f"{tag:<10} {snap_id:>8} {boundary:>8} {value:>6}")

    # GC: while the long snapshot lives, its version survives.
    removed = store.gc()
    print(f"\ngc() with long snapshot active: removed={removed}, "
          f"history={[v.value for v in store.version_history('acct')]}")
    long_txn.abort()
    removed = store.gc()
    print(f"gc() after long txn closed:     removed={removed}, "
          f"history={[v.value for v in store.version_history('acct')]}")


if __name__ == "__main__":
    main()
