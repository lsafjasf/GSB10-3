"""Self-test + demo for timing_wheel.py.

Run:  python3 selftest.py
Writes trigger-order data to trigger_order.json.
Every scenario asserts fire times, insertion order and lazy-cancel counts.
"""

from __future__ import annotations

import json
import os
import random

from timing_wheel import TimingWheel, TimerNode, VirtualClock


def scenario_ordering_and_cascade(data: dict) -> None:
    """Immediate timers, multi-level downgrade, batch same-tick ordering."""
    clock = VirtualClock(0)
    wheel = TimingWheel(clock, bits=8)
    log: list = []
    nodes: dict = {}

    def schedule(tag: str, expiry: int) -> TimerNode:
        node = wheel.schedule_at(
            expiry, lambda nd, tag=tag: log.append((wheel.now, tag))
        )
        nodes[tag] = node
        return node

    # immediate (delay 0), fired at tick 0 in insertion order
    schedule("imm_a", 0)
    schedule("imm_b", 0)

    schedule("lv0", 5)           # straight into level 0
    schedule("lv1", 300)         # level 1, cascades 1 -> 0 at tick 256
    schedule("lv2_edge", 65537)  # level 2 slot 0, 2 -> 0 at boundary tick
    schedule("lv2_off", 66048)   # level 2, true 2 -> 1 -> 0 over two cascades

    # same-tick batch: two timers from level 1 ...
    schedule("batch_a", 512)
    schedule("batch_b", 512)

    # lazy-cancel a timer that never reaches its expiry tick
    cancelled = schedule("cancelled", 257)
    cancelled.cancel()

    wheel.pump()  # immediate timers fire without moving time
    assert log == [(0, "imm_a"), (0, "imm_b")]
    assert cancelled.fired == 0, "cancelled timer must never fire"

    wheel.advance(5)
    assert log[-1] == (5, "lv0")

    wheel.advance(300 - 5)  # level-1 cascade happens at tick 256, fire at 300
    assert log[-1] == (300, "lv1")
    assert nodes["lv1"].placements == [1, 0]

    # ... and a third timer inserted directly into level 0 *after* tick 256;
    # the group at tick 512 must still fire in global insertion order
    schedule("batch_c", 512)
    wheel.advance(512 - 300)
    assert [tag for _, tag in log[-3:]] == ["batch_a", "batch_b", "batch_c"]
    assert all(tick == 512 for tick, _ in log[-3:])
    assert nodes["batch_a"].placements == [1, 0]

    wheel.advance(65537 - 512)  # bulk-skip empty ticks, boundary cascade at 65536
    assert log[-1] == (65537, "lv2_edge")
    assert nodes["lv2_edge"].placements == [2, 0], nodes["lv2_edge"].placements

    wheel.advance(66048 - 65537)
    assert log[-1] == (66048, "lv2_off")
    assert nodes["lv2_off"].placements == [2, 1, 0], nodes["lv2_off"].placements
    assert cancelled.fired == 0
    assert wheel.pending() == 0

    data["ordering"] = {
        "description": "immediate, level 1/2 cascading downgrade, same-tick batch",
        "placements": {
            tag: {"expiry": node.expiry, "placement_chain": list(node.placements)}
            for tag, node in nodes.items()
        },
        "trigger_order": [tag for _, tag in log],
        "trigger_times": [tick for tick, _ in log],
        "cancelled_tag": "cancelled",
        "cancelled_fire_count": cancelled.fired,
    }


def scenario_mass_cancel(data: dict) -> None:
    """Thousands of timers spread across levels; cancel ~75% of them."""
    clock = VirtualClock(0)
    wheel = TimingWheel(clock, bits=8)
    log: list = []
    rng = random.Random(20260104)
    n = 4000
    horizon = 20000  # spans levels 0 and 1

    nodes: list[TimerNode] = []
    for i in range(n):
        expiry = 1 + rng.randrange(horizon)
        node = wheel.schedule_at(
            expiry, lambda nd, i=i: log.append((wheel.now, i))
        )
        nodes.append(node)

    cancelled_ids = sorted(rng.sample(range(n), k=3 * n // 4))
    cancelled_set = set(cancelled_ids)
    for i in cancelled_ids:
        assert nodes[i].cancel(), "first cancel() must return True"
    for i in cancelled_ids[:100]:
        assert nodes[i].cancel() is False, "cancel() must be idempotent"

    # cancel a timer already sitting in the immediate (delay-0) bucket
    immediate = wheel.schedule_after(0, lambda nd: log.append((wheel.now, "imm")))
    assert immediate.cancel()
    wheel.advance(horizon + 1)

    fired = log
    assert immediate.fired == 0
    assert set(i for _, i in fired).isdisjoint(cancelled_set), "cancelled job fired!"

    for tick, i in fired:
        assert nodes[i].fired == 1
        assert tick == nodes[i].expiry, f"job {i} fired at {tick}, want {nodes[i].expiry}"
    for i in cancelled_ids:
        assert nodes[i].fired == 0, f"cancelled job {i} fired {nodes[i].fired} times"

    # strict insertion order among jobs sharing one expiry tick
    by_expiry: dict = {}
    for tick, i in fired:
        by_expiry.setdefault(tick, []).append(nodes[i].seq)
    assert all(seq == sorted(seq) for seq in by_expiry.values())

    data["mass_cancel"] = {
        "description": "4000 timers across levels 0-1, 75% lazily cancelled",
        "scheduled": n,
        "cancelled": len(cancelled_ids),
        "fired": len(fired),
        "sample_trigger_order": [i for _, i in fired[:20]],
        "cancelled_fire_counts_all_zero": True,
        "all_fired_at_their_expiry_tick": True,
    }


def scenario_huge_timeout(data: dict) -> None:
    """Delay larger than one full level-3 round: downgrade all the way."""
    clock = VirtualClock(0)
    wheel = TimingWheel(clock, bits=8)
    log: list = []
    nodes: dict = {}

    def schedule(tag: str, expiry: int) -> TimerNode:
        node = wheel.schedule_at(
            expiry, lambda nd, tag=tag: log.append((wheel.now, tag))
        )
        nodes[tag] = node
        return node

    near = 256 ** 3 + 7            # level 3 -> 0
    far = 256 ** 4 + 42            # level 4 -> 0 (single boundary cascade)
    layered = 256 ** 4 + 256 ** 2 + 512  # level 4 -> 2 -> 1 -> 0

    schedule("near", near)
    schedule("far", far)
    schedule("far_same1", far)
    schedule("far_same2", far)
    schedule("layered", layered)

    assert nodes["near"].placements[0] == 3
    assert nodes["far"].placements[0] == 4
    assert nodes["layered"].placements[0] == 4
    assert wheel.levels == 5

    wheel.advance(near)  # bulk-skip empty ticks instead of 16 million steps
    assert log == [(near, "near")]
    assert nodes["near"].placements == [3, 0]

    wheel.advance(far - near)
    assert [tag for _, tag in log[1:]] == ["far", "far_same1", "far_same2"]
    assert all(tick == far for tick, _ in log[1:])
    assert nodes["far"].placements == [4, 0]

    wheel.advance(layered - far)
    assert log[-1] == (layered, "layered")
    assert nodes["layered"].placements == [4, 2, 1, 0]

    data["huge_timeout"] = {
        "description": "timeout beyond one full level-3 round, bulk tick skipping",
        "ticks_skipped": layered,
        "placement_chain": {
            tag: list(node.placements) for tag, node in nodes.items()
        },
        "trigger_order": [tag for _, tag in log],
        "trigger_times": [tick for tick, _ in log],
    }


def main() -> None:
    data: dict = {}
    scenario_ordering_and_cascade(data)
    scenario_mass_cancel(data)
    scenario_huge_timeout(data)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trigger_order.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)

    print("all self-test assertions passed")
    print("trigger-order data written to", out)
    for name, result in data.items():
        print(f"- {name}:", result.get("trigger_order", "<see json>"))


if __name__ == "__main__":
    main()
