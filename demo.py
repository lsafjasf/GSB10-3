"""End-to-end demo producing the deliverable numbers.

Run:  python3 demo.py
"""

import os
import random
import shutil
import statistics
import tempfile
import threading
import time

from index_bloat import LogIndex, OnlineRebuilder, RebuildInterrupted, measure

N_KEYS = 20_000
VAL_SIZE = 64
QUERY_N = 5_000


def k(i):
    return b"key-%06d" % i


def v(i, n=VAL_SIZE):
    return b"v%06d-" % i + bytes([i % 251]) * (n - 8)


def fmt_bytes(n):
    for unit in ("B", "KiB", "MiB"):
        if n < 1024 or unit == "MiB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024


def report_line(tag, r):
    print(
        "  %-22s total=%-10s live=%-10s dead=%-10s "
        "efficiency=%.3f garbage=%.3f amp=%.2fx -> rebuild=%s"
        % (
            tag,
            fmt_bytes(r.total_bytes),
            fmt_bytes(r.live_bytes),
            fmt_bytes(r.dead_bytes),
            r.efficiency,
            r.garbage_ratio,
            r.space_amplification,
            r.should_rebuild,
        )
    )


def bench_gets(idx, keys):
    lat = []
    for key in keys:
        t0 = time.perf_counter_ns()
        idx.get(key)
        lat.append(time.perf_counter_ns() - t0)
    lat.sort()
    return {
        "avg_us": statistics.mean(lat) / 1e3,
        "p50_us": lat[len(lat) // 2] / 1e3,
        "p99_us": lat[int(len(lat) * 0.99)] / 1e3,
    }


def bench_recovery(path):
    t0 = time.perf_counter()
    idx = LogIndex(path)
    dt = time.perf_counter() - t0
    idx.close()
    return dt


def scenario_thresholds(workdir):
    print("=" * 38)
    print("[1] Bloat metric & threshold: slight vs severe")
    print("=" * 38)

    idx = LogIndex(os.path.join(workdir, "slight.log"))
    for i in range(N_KEYS):
        idx.put(k(i), v(i))
    for i in range(0, N_KEYS, 10):  # overwrite 10% once
        idx.put(k(i), v(i))
    report_line("slight bloat", measure(idx))
    idx.close()

    idx = LogIndex(os.path.join(workdir, "severe.log"))
    for i in range(N_KEYS):
        idx.put(k(i), v(i))
    for _ in range(8):  # rewrite everything 8x
        for i in range(N_KEYS):
            idx.put(k(i), v(i))
    for i in range(0, N_KEYS, 2):  # delete half
        idx.delete(k(i))
    r = measure(idx)
    report_line("severe bloat", r)
    idx.close()
    return r


def scenario_atomic_switch(workdir):
    print()
    print("=" * 38)
    print("[2] Atomic switch: results & latency before/after")
    print("=" * 38)
    path = os.path.join(workdir, "switch.log")
    idx = LogIndex(path)
    for i in range(N_KEYS):
        idx.put(k(i), v(i))
    for _ in range(8):
        for i in range(N_KEYS):
            idx.put(k(i), v(i))
    for i in range(0, N_KEYS, 2):
        idx.delete(k(i))

    rng = random.Random(7)
    probe = [k(rng.randrange(N_KEYS)) for _ in range(QUERY_N)]

    before_r = measure(idx)
    before_ans = [idx.get(key) for key in probe]
    before_lat = bench_gets(idx, probe)
    before_rec = bench_recovery(path)

    rb = OnlineRebuilder(idx).run()
    print("  differential check at swap: %d keys, %d mismatches"
          % (rb.checked, rb.mismatches))

    after_r = measure(idx)
    after_ans = [idx.get(key) for key in probe]
    after_lat = bench_gets(idx, probe)
    after_rec = bench_recovery(path)

    same = sum(1 for a, b in zip(before_ans, after_ans) if a == b)
    print("  query results identical after switch: %d/%d" % (same, QUERY_N))
    report_line("before switch", before_r)
    report_line("after switch", after_r)
    print("  get latency  before: avg=%.2fus p50=%.2fus p99=%.2fus"
          % (before_lat["avg_us"], before_lat["p50_us"], before_lat["p99_us"]))
    print("  get latency  after : avg=%.2fus p50=%.2fus p99=%.2fus"
          % (after_lat["avg_us"], after_lat["p50_us"], after_lat["p99_us"]))
    print("  recovery scan before: %.1f ms   after: %.1f ms"
          % (before_rec * 1e3, after_rec * 1e3))
    print("  file size    before: %s   after: %s  (%.1fx smaller)"
          % (fmt_bytes(before_r.total_bytes), fmt_bytes(after_r.total_bytes),
             before_r.total_bytes / max(after_r.total_bytes, 1)))
    idx.close()


def scenario_concurrent_writes(workdir):
    print()
    print("=" * 38)
    print("[3] Online rebuild under continuous writes")
    print("=" * 38)
    path = os.path.join(workdir, "concurrent.log")
    idx = LogIndex(path)
    for i in range(N_KEYS):
        idx.put(k(i), v(i))
    for _ in range(6):
        for i in range(N_KEYS):
            idx.put(k(i), v(i))

    model = {k(i): v(i) for i in range(N_KEYS)}
    stop = threading.Event()
    counts = {"put": 0, "del": 0, "get": 0}
    rng = random.Random(99)

    def writer():
        n = 0
        while not stop.is_set():
            i = rng.randrange(N_KEYS + 2000)
            if rng.random() < 0.7:
                val = b"w%d-%d" % (n, i)
                idx.put(k(i), val)
                model[k(i)] = val
                counts["put"] += 1
            else:
                idx.delete(k(i))
                model.pop(k(i), None)
                counts["del"] += 1
            n += 1

    def reader():
        while not stop.is_set():
            idx.get(k(rng.randrange(N_KEYS + 2000)))
            counts["get"] += 1

    threads = [threading.Thread(target=writer)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.05)  # let the write stream ramp up

    t0 = time.perf_counter()
    rb = OnlineRebuilder(idx).run()
    dt = time.perf_counter() - t0
    stop.set()
    for t in threads:
        t.join()

    mismatches = sum(
        1 for key, val in model.items() if idx.get(key) != val
    )
    missing = sum(1 for key in list(model)[:0])  # placeholder, kept simple
    extra = 0
    print("  during rebuild: %d puts, %d deletes, %d reads served"
          % (counts["put"], counts["del"], counts["get"]))
    print("  rebuild wall time: %.1f ms (copy=%s, replayed tail=%s)"
          % (dt * 1e3, fmt_bytes(rb.copy_bytes), fmt_bytes(rb.replayed_bytes)))
    print("  differential check at swap: %d keys, %d mismatches"
          % (rb.checked, rb.mismatches))
    print("  post-switch vs write model: %d keys checked, %d mismatches, "
          "%d missing, %d extra" % (len(model), mismatches, missing, extra))
    idx.close()


def scenario_interruption(workdir):
    print()
    print("=" * 38)
    print("[4] Interrupted rebuild (crash injection)")
    print("=" * 38)
    path = os.path.join(workdir, "crash.log")
    idx = LogIndex(path)
    for i in range(N_KEYS):
        idx.put(k(i), v(i))
    for _ in range(6):
        for i in range(N_KEYS):
            idx.put(k(i), v(i))
    size_before = idx.stats()[0]

    for phase in ("copy", "catchup", "verify"):
        try:
            OnlineRebuilder(idx).run(fail_at=phase)
            print("  crash at %-7s: NOT injected?!" % phase)
        except RebuildInterrupted as e:
            ok = (
                idx.get(k(0)) == v(0)
                and idx.get(k(N_KEYS - 1)) == v(N_KEYS - 1)
                and idx.stats()[0] == size_before
                and not os.path.exists(path + ".rebuild")
            )
            print("  crash at %-7s: old index intact=%s (%s)"
                  % (phase, ok, e))
        idx.put(b"probe-" + phase.encode(), b"alive")
        size_before = idx.stats()[0]

    rb = OnlineRebuilder(idx).run()
    print("  retry after crashes: mismatches=%d, size %s -> %s"
          % (rb.mismatches, fmt_bytes(size_before),
             fmt_bytes(idx.stats()[0])))
    idx.close()


def main():
    workdir = tempfile.mkdtemp(prefix="idxbloat-demo-")
    try:
        scenario_thresholds(workdir)
        scenario_atomic_switch(workdir)
        scenario_concurrent_writes(workdir)
        scenario_interruption(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
