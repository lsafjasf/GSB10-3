"""Deterministic simulation of N cores contending for one spin lock.

Single-threaded discrete-event model (simulated time is injectable, so the
benchmark is reproducible and uses no real threads):

* an acquire attempt (a failed CAS) costs 1 spin slot and 0 extra delay;
* the lock holder keeps the lock for cs_len time units (critical section);
* after a failed attempt the contender sleeps the policy delay, or, once the
  policy escalates to YIELD/BLOCK, is woken exactly when the lock frees;
* after release a contender re-enters immediately (maximal contention);
  ties are broken by fewest acquisitions to avoid starvation artifacts.

Run:  python3 simulate.py
"""

import argparse
import heapq

from spin_backoff import (
    BLOCK,
    ExponentialBackoff,
    ExponentialJitterBackoff,
    FixedBackoff,
    SLEEP,
)
import random


def simulate(n_threads, cs_len, rounds, policy_factory, seed=0):
    """Return dict of attempt/escalation counters for one configuration."""
    policies = [policy_factory(i, seed) for i in range(n_threads)]
    ready = [0.0] * n_threads
    attempts = [0] * n_threads
    acquired = [0] * n_threads
    heap = [(0.0, 0, i) for i in range(n_threads)]
    heapq.heapify(heap)

    lock_free_at = 0.0
    done = 0
    total = n_threads * rounds

    while done < total:
        t, _, i = heapq.heappop(heap)
        attempts[i] += 1
        if lock_free_at <= t:
            lock_free_at = t + cs_len
            acquired[i] += 1
            done += 1
            policies[i].reset()
            nxt = lock_free_at
        else:
            action, delay = policies[i].step()
            if action == SLEEP:
                # jitter may return 0; a 1e-6 floor guarantees event progress
                nxt = t + max(delay, 1e-6)
            else:
                nxt = lock_free_at  # YIELD/BLOCK: wake on release
        heapq.heappush(heap, (nxt, acquired[i], i))

    return {
        "threads": n_threads,
        "cs_len": cs_len,
        "rounds": rounds,
        "total_attempts": sum(attempts),
        "attempts_per_acquire": sum(attempts) / total,
        "escalations": sum(p.escalations for p in policies),
    }


def default_factories(cap=64.0, max_spins=12):
    return [
        ("fixed(delay=1)", lambda i, seed: FixedBackoff(delay=1.0, cap=cap,
                                                        max_spins=max_spins)),
        ("exponential(base=1)", lambda i, seed: ExponentialBackoff(
            base=1.0, cap=cap, max_spins=max_spins)),
        ("exp+jitter(base=1)", lambda i, seed: ExponentialJitterBackoff(
            base=1.0, cap=cap, max_spins=max_spins,
            rng=random.Random(seed + 1000 + i))),
    ]


def run_matrix(core_counts, cs_lengths, rounds, factories):
    rows = []
    for n in core_counts:
        for cs in cs_lengths:
            for name, factory in factories:
                stats = simulate(n, cs, rounds, factory)
                stats["policy"] = name
                rows.append(stats)
    return rows


def format_table(rows):
    header = (f"{'cores':>5} {'cs_len':>6} {'policy':<22} "
              f"{'attempts':>10} {'att/acq':>9} {'escalations':>12}")
    out = [header, "-" * len(header)]
    for r in rows:
        out.append(
            f"{r['threads']:>5} {r['cs_len']:>6} {r['policy']:<22} "
            f"{r['total_attempts']:>10} {r['attempts_per_acquire']:>9.2f} "
            f"{r['escalations']:>12}"
        )
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cores", default="1,2,4,8,16,32")
    ap.add_argument("--cs-lengths", default="1,10,50")
    ap.add_argument("--rounds", type=int, default=100)
    args = ap.parse_args()
    cores = [int(x) for x in args.cores.split(",")]
    cs_lengths = [int(x) for x in args.cs_lengths.split(",")]
    rows = run_matrix(cores, cs_lengths, args.rounds, default_factories())
    print(f"rounds per core: {args.rounds}  (total acquires = cores*rounds)")
    print(format_table(rows))


if __name__ == "__main__":
    main()
