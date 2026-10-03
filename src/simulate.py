"""Scenario simulation: prints batch-size trajectories and stability data.

All scenarios are deterministic (no RNG), simulated time is advanced by
the batch latency and fed to the sizer via an injectable clock.

Run:  python3 src/simulate.py
Writes CSVs into ./data and prints summary tables.
"""

from __future__ import annotations

import csv
import math
import os
import statistics
from typing import Callable, List, Tuple

from adaptive_batch import AdaptiveBatchSizer

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

MIN_SIZE, MAX_SIZE, TARGET = 10, 500, 0.20


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
    def time(self) -> float:
        return self.t
    def advance(self, dt: float) -> None:
        self.t += dt


def jitter(seq: int, amp: float = 0.05) -> float:
    """Deterministic triangular-ish noise, +/-amp, zero mean over 8 steps."""
    return amp * (seq % 7 - 3) / 3.0


def simulate(
    name: str,
    per_item_cost: Callable[[int], float],
    fail_rate_fn: Callable[[int], float],
    batches: int = 120,
    **kw,
) -> Tuple[AdaptiveBatchSizer, List[dict]]:
    clock = FakeClock()
    sizer = AdaptiveBatchSizer(
        min_size=MIN_SIZE, max_size=MAX_SIZE, target_latency=TARGET, now=clock.time, **kw
    )
    rows = []
    shock_at = None
    for i in range(batches):
        n = sizer.batch_size
        cost = per_item_cost(i)
        if name == "sudden_slowdown" and shock_at is None and cost > BASE_COST * 1.5:
            shock_at = i
        fr = fail_rate_fn(i)
        lat = cost * n * (1.0 + jitter(i))
        lat = max(lat, 0.0)
        failures = int(round(n * fr))
        clock.advance(lat)
        old = n
        sizer.record(lat, failures)
        rows.append(dict(seq=i + 1, old=old, new=sizer.batch_size,
                         reason=sizer.events[-1].reason,
                         latency_ms=round(lat * 1000, 2),
                         ema_ms=round(sizer.events[-1].lat_ema * 1000, 2),
                         fail_pct=round(100 * sizer.events[-1].fail_ema, 2),
                         t_ms=round(clock.time() * 1000, 1)))
    return sizer, rows


BASE_COST = 0.001   # 1 ms per item => batch of 200 = 0.2 s target


def stability_summary(rows: List[dict], tail: int = 40) -> dict:
    sizes = [r["new"] for r in rows]
    tail_sizes = sizes[-tail:]
    adjusts = [(r["old"], r["new"], r["reason"]) for r in rows[-tail:] if r["old"] != r["new"]]

    # max streak of consecutive adjustments in the same direction (whole run)
    best, streak, prev = 0, 0, 0
    for r in rows:
        d = r["new"] - r["old"]
        sgn = (d > 0) - (d < 0)
        if sgn:
            streak = streak + 1 if sgn == prev else 1
            best, prev = max(best, streak), sgn

    # direction reversals (up immediately followed by down, or vice-versa)
    dirs = [(r["new"] > r["old"]) - (r["new"] < r["old"]) for r in rows]
    dirs = [d for d in dirs if d != 0]
    reversals = sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)

    mean = statistics.mean(tail_sizes)
    stdev = statistics.pstdev(tail_sizes)
    return {
        "tail_adjusts": len(adjusts),
        "max_consecutive_adjusts": best,
        "reversals_total": reversals,
        "tail_mean": round(mean, 1),
        "tail_std": round(stdev, 2),
        "tail_cv_pct": round(100 * stdev / mean, 2),
        "tail_range": (min(tail_sizes), max(tail_sizes)),
    }


def print_trajectory(name: str, rows: List[dict], picked: List[int]) -> None:
    print(f"\n--- {name}: batch size before -> after (picked batches) ---")
    print(" seq |  old ->  new | reason    | lat(ms) | ema(ms) | fail% ")
    for i in picked:
        r = rows[i]
        print(f"{r['seq']:4d} | {r['old']:4d} -> {r['new']:4d} | {r['reason']:<9} | "
              f"{r['latency_ms']:7.1f} | {r['ema_ms']:7.1f} | {r['fail_pct']:5.1f}")


def save_csv(name: str, rows: List[dict]) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{name}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def main() -> None:
    scenarios = {}

    # 1) stable load: 1 ms/item, no failures
    scenarios["stable_load"] = simulate(
        "stable_load",
        per_item_cost=lambda i: BASE_COST,
        fail_rate_fn=lambda i: 0.0,
        batches=120,
    )

    # 2) sudden slowdown at batch 40: 1ms -> 4ms/item (4x)
    def slow_cost(i):
        return BASE_COST if i < 40 else BASE_COST * 4
    scenarios["sudden_slowdown"] = simulate(
        "sudden_slowdown", per_item_cost=slow_cost, fail_rate_fn=lambda i: 0.0, batches=120
    )

    # 3) persistent failures from batch 40 on (30% of items fail)
    scenarios["persistent_failures"] = simulate(
        "persistent_failures",
        per_item_cost=lambda i: BASE_COST,
        fail_rate_fn=lambda i: 0.0 if i < 40 else 0.30,
        batches=120,
    )

    # 4) load drop at batch 40: 1ms -> 0.25ms/item (4x faster)
    def drop_cost(i):
        return BASE_COST if i < 40 else BASE_COST * 0.25
    scenarios["load_drop"] = simulate(
        "load_drop", per_item_cost=drop_cost, fail_rate_fn=lambda i: 0.0, batches=120
    )

    picks_stable = [0, 4, 9, 14, 19, 24, 29, 39, 79, 119]
    picks_shock = [37, 38, 39, 40, 41, 42, 43, 44, 50, 60, 90, 119]

    paths = []
    for name, (sizer, rows) in scenarios.items():
        paths.append(save_csv(name, rows))
        picked = picks_shock if name in ("sudden_slowdown", "persistent_failures", "load_drop") else picks_stable
        print_trajectory(name, rows, picked)
        st = stability_summary(rows)
        print(" stability(last 40 batches):", st)

    # ---- response speed for sudden slowdown ----
    rows = scenarios["sudden_slowdown"][1]
    onset = next(r for r in rows if r["seq"] == 41)          # first batch under 4x cost
    eq_size = TARGET / (BASE_COST * 4)                        # new equilibrium ~ 50
    recovered = next((r for r in rows[40:] if r["ema_ms"] / 1000.0 <= TARGET * 1.15), None)
    eq_lo, eq_hi = eq_size * 0.85, eq_size * 1.15
    in_eq = next((r for r in rows[40:] if eq_lo <= r["new"] <= eq_hi), None)
    print("\n--- sudden_slowdown response speed ---")
    print(f" theoretical new equilibrium size = {eq_size:.0f} (target {TARGET*1000:.0f} ms / 4 ms per item)")
    print(f" reaction: FIRST batch after shock cut {onset['old']} -> {onset['new']} "
          f"(raw lat {onset['latency_ms']:.0f} ms vs target {TARGET*1000:.0f} ms)")
    print(f" entered equilibrium band [{eq_lo:.0f}, {eq_hi:.0f}] in {in_eq['seq']-40} batch(es) after shock, "
          f"size={in_eq['new']}")
    print(f" latency EMA back inside target band (<= {TARGET*1.15*1000:.0f} ms) "
          f"{recovered['seq']-40} batches after shock")

    # ---- persistent failure response ----
    rows = scenarios["persistent_failures"][1]
    at_min = next((r for r in rows[40:] if r["new"] == MIN_SIZE), None)
    held = all(r["new"] == MIN_SIZE for r in rows[at_min["seq"] - 1:])
    print("\n--- persistent_failures response ---")
    print(f" reached min size {MIN_SIZE} in {at_min['seq']-40} batches; stayed at min afterwards: {held}")

    # ---- load drop response ----
    rows = scenarios["load_drop"][1]
    eq = TARGET / (BASE_COST * 0.25)
    at_max = next((r for r in rows[40:] if r["new"] == MAX_SIZE), None)
    print("\n--- load_drop response ---")
    print(f" grew to max {MAX_SIZE} in {at_max['seq']-40} batches (geometric +10% per batch); "
          f"equilibrium would be {eq:.0f}, bounded at max")

    print("\nCSV files:")
    for p in paths:
        print(" ", p)


if __name__ == "__main__":
    main()
