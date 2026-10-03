from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from midi_events import ParseError, canonical_event, encode_vlq, merge_tracks, parse_tracks
from reference_parser import ReferenceParseError, parse_and_merge_reference


@dataclass
class FuzzCase:
    tracks: List[List[bytes]]
    cut: Optional[int]


def nonminimal_vlq(value: int, rng: random.Random) -> bytes:
    encoded = encode_vlq(value)
    extra = rng.randrange(3)
    return b"\x80" * extra + encoded


def random_payload(rng: random.Random, max_length: int = 8) -> bytes:
    return bytes(rng.randrange(256) for _ in range(rng.randrange(max_length + 1)))


def make_event(rng: random.Random, running_status: Optional[int]) -> Tuple[bytes, Optional[int]]:
    if running_status is not None and rng.random() < 0.28:
        length = 1 if running_status & 0xF0 in (0xC0, 0xD0) else 2
        return bytes(rng.randrange(128) for _ in range(length)), running_status

    choice = rng.randrange(10)

    if choice < 6:
        high_nibble = rng.choice((0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0))
        status = high_nibble | rng.randrange(16)
        length = 1 if high_nibble in (0xC0, 0xD0) else 2
        payload = bytes(rng.randrange(128) for _ in range(length))
        return bytes((status,)) + payload, status

    if choice < 8:
        meta_type = rng.choice(
            (0x01, 0x03, 0x05, 0x06, 0x10, 0x20, 0x2F, 0x51, 0x58, 0x59, 0x7E, 0x7F)
        )
        payload = b"" if meta_type == 0x2F else random_payload(rng, 20)
        length = nonminimal_vlq(len(payload), rng) if rng.random() < 0.10 else encode_vlq(len(payload))
        return b"\xff" + bytes((meta_type,)) + length + payload, None

    if choice < 9:
        status = rng.choice((0xF0, 0xF7))
        payload = random_payload(rng, 12)
        return bytes((status,)) + encode_vlq(len(payload)) + payload, None

    status = rng.choice((0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF8, 0xFA, 0xFC, 0xFE))
    if status == 0xF2:
        payload = bytes(rng.randrange(128) for _ in range(2))
    elif status in (0xF1, 0xF3):
        payload = bytes((rng.randrange(128),))
    else:
        payload = b""
    return bytes((status,)) + payload, None


def random_delta(rng: random.Random) -> int:
    if rng.random() < 0.20:
        special = (0, 1, 0x7F, 0x80, 0x3FFF, 0x4000, 0x0FFFFFFF, 0x10000000)
        return rng.choice(special)
    if rng.random() < 0.15:
        return 1 << rng.randrange(28, 48)
    return rng.randrange(100000)


def generate_case(rng: random.Random, max_events: int) -> FuzzCase:
    tracks: List[List[bytes]] = []
    for _ in range(rng.randrange(5)):
        events = []
        running_status: Optional[int] = None
        for _ in range(rng.randrange(max_events + 1)):
            delta = random_delta(rng)
            delta_raw = nonminimal_vlq(delta, rng) if rng.random() < 0.10 else encode_vlq(delta)
            raw, running_status = make_event(rng, running_status)
            events.append(delta_raw + raw)
        tracks.append(events)

    total_length = sum(sum(len(event) for event in track) for track in tracks)
    cut = rng.randrange(total_length + 1) if rng.random() < 0.35 else None
    return FuzzCase(tracks=tracks, cut=cut)


def complete_track_bytes(tracks: Sequence[Sequence[bytes]]) -> List[bytes]:
    return [b"".join(events) for events in tracks]


def apply_cut(tracks: Sequence[Sequence[bytes]], cut: Optional[int]) -> List[bytes]:
    complete = complete_track_bytes(tracks)
    if cut is None:
        return complete

    result = []
    remaining = cut
    for track in complete:
        if remaining <= 0:
            result.append(b"")
        elif remaining < len(track):
            result.append(track[:remaining])
            remaining = 0
        else:
            result.append(track)
            remaining -= len(track)
    return result


def outcome_for_case(case: FuzzCase) -> Tuple[Tuple, Tuple]:
    track_bytes = apply_cut(case.tracks, case.cut)
    try:
        parsed = parse_tracks(track_bytes)
        library = ("ok", tuple(canonical_event(event) for event in merge_tracks(parsed)))
    except ParseError as error:
        library = ("error", error.reason, error.offset)

    try:
        reference = ("ok", parse_and_merge_reference(track_bytes))
    except ReferenceParseError as error:
        reference = ("error", error.reason, error.offset)

    return library, reference


def mismatches(case: FuzzCase) -> Optional[Tuple[Tuple, Tuple]]:
    library, reference = outcome_for_case(case)
    return None if library == reference else (library, reference)


def normalize_cut(case: FuzzCase) -> FuzzCase:
    total = sum(len(track) for track in complete_track_bytes(case.tracks))
    if case.cut is not None and case.cut >= total:
        return FuzzCase(case.tracks, None)
    return case


def describe_outcome(outcome: Tuple, limit: int = 400) -> str:
    text = repr(outcome)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def minimize_case(case: FuzzCase, mismatch: Tuple[Tuple, Tuple]) -> Tuple[FuzzCase, Tuple[Tuple, Tuple], List[str]]:
    current = normalize_cut(FuzzCase([list(track) for track in case.tracks], case.cut))
    current_mismatch = mismatch
    steps: List[str] = []

    boundaries = []
    total = 0
    for track in complete_track_bytes(current.tracks):
        total += len(track)
        boundaries.append(total)

    for candidate_cut in [None] + boundaries:
        if candidate_cut == current.cut:
            continue
        candidate = FuzzCase(current.tracks, candidate_cut)
        candidate_mismatch = mismatches(candidate)
        if candidate_mismatch is not None:
            steps.append(
                f"cut {current.cut} -> {candidate_cut}; mismatch remains"
            )
            current = candidate
            current_mismatch = candidate_mismatch
            break

    changed = True
    while changed:
        changed = False
        for track_index in range(len(current.tracks)):
            event_index = 0
            while event_index < len(current.tracks[track_index]):
                candidate_tracks = [list(track) for track in current.tracks]
                del candidate_tracks[track_index][event_index]
                candidate = normalize_cut(FuzzCase(candidate_tracks, current.cut))
                candidate_mismatch = mismatches(candidate)
                if candidate_mismatch is not None:
                    steps.append(
                        f"remove track {track_index} event {event_index}; mismatch remains"
                    )
                    current = candidate
                    current_mismatch = candidate_mismatch
                    changed = True
                else:
                    event_index += 1

    track_index = 0
    while track_index < len(current.tracks):
        candidate_tracks = [list(track) for track in current.tracks]
        del candidate_tracks[track_index]
        candidate = normalize_cut(FuzzCase(candidate_tracks, current.cut))
        candidate_mismatch = mismatches(candidate)
        if candidate_mismatch is not None:
            steps.append(f"remove full track {track_index}; mismatch remains")
            current = candidate
            current_mismatch = candidate_mismatch
        else:
            track_index += 1

    return current, current_mismatch, steps


def case_to_json(case: FuzzCase) -> dict:
    return {
        "tracks_hex": [track.hex() for track in apply_cut(case.tracks, case.cut)],
        "cut": case.cut,
    }


def save_failure(
    path: Path,
    seed: int,
    index: int,
    original: FuzzCase,
    minimized: FuzzCase,
    mismatch: Tuple[Tuple, Tuple],
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "seed": seed,
        "case_index": index,
        "original": case_to_json(original),
        "minimized": case_to_json(minimized),
        "library": repr(mismatch[0]),
        "reference": repr(mismatch[1]),
    }
    (path / "mismatch.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run(seed: int, cases: int, max_events: int, artifact_dir: Path) -> bool:
    rng = random.Random(seed)
    truncated = 0
    total_events = 0

    for index in range(cases):
        case = generate_case(rng, max_events)
        if case.cut is not None:
            truncated += 1
        total_events += sum(len(track) for track in case.tracks)

        mismatch = mismatches(case)
        if mismatch is None:
            continue

        print(f"MISMATCH seed={seed} index={index}")
        print(f"library:   {describe_outcome(mismatch[0])}")
        print(f"reference: {describe_outcome(mismatch[1])}")
        print("convergence:")
        minimized, minimized_mismatch, steps = minimize_case(case, mismatch)
        if steps:
            for step_number, step in enumerate(steps, 1):
                print(f"  {step_number:02d}. {step}")
        else:
            print("  case was already irreducible")
        save_failure(artifact_dir, seed, index, case, minimized, minimized_mismatch)
        print(f"minimized case saved to {artifact_dir / 'mismatch.json'}")
        return False

    print(
        f"OK: {cases} cases, {total_events} events, "
        f"{truncated} cross-track truncations; all outcomes are identical"
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Differential-test MIDI event merging")
    parser.add_argument("--seed", type=int, default=10303)
    parser.add_argument("--cases", type=int, default=500)
    parser.add_argument("--max-events", type=int, default=24)
    parser.add_argument("--artifact-dir", type=Path, default=Path("differential_artifacts"))
    args = parser.parse_args()
    raise SystemExit(0 if run(args.seed, args.cases, args.max_events, args.artifact_dir) else 1)


if __name__ == "__main__":
    main()
